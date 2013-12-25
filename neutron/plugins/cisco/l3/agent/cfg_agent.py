# vim: tabstop=4 shiftwidth=4 softtabstop=4
#
# Copyright 2013 Cisco Systems, Inc.  All rights reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.
#
# @author: Hareesh Puthalath, Cisco Systems, Inc.

import eventlet
import netaddr
from oslo.config import cfg

from neutron.agent.common import config
from neutron.agent.linux import external_process
from neutron.agent.linux import interface
from neutron.agent.linux import ip_lib
from neutron.agent import rpc as agent_rpc
from neutron.common import constants as l3_constants
from neutron.common import topics
from neutron.common import utils as common_utils
from neutron import context
from neutron import manager
from neutron import service as neutron_service
from neutron.openstack.common import lockutils
from neutron.openstack.common import log as logging
from neutron.openstack.common import loopingcall
from neutron.openstack.common import periodic_task
from neutron.openstack.common.rpc import common as rpc_common
from neutron.openstack.common.rpc import proxy
from neutron.openstack.common import service
from neutron.openstack.common import excutils
from neutron.plugins.cisco.l3.common import constants as cl3_constants
from neutron.plugins.cisco.l3.agent.router_info import RouterInfo
from neutron.plugins.cisco.l3.agent.hosting_devices_manager import (
    HostingDevicesManager)

LOG = logging.getLogger(__name__)

RPC_LOOP_INTERVAL = 1


class L3PluginApi(proxy.RpcProxy):
    """Agent side of the l3 agent RPC API."""

    BASE_RPC_API_VERSION = '1.0'

    def __init__(self, topic, host):
        super(L3PluginApi, self).__init__(
            topic=topic, default_version=self.BASE_RPC_API_VERSION)
        self.host = host

    def get_routers(self, context, router_ids=None, hd_ids=[]):
        """Make a remote process call to retrieve the sync data for routers."""
        return self.call(context,
                         self.make_msg('cfg_sync_routers', host=self.host,
                                       router_ids=router_ids,
                                       hosting_device_ids=hd_ids),
                         topic=self.topic)

    def get_external_network_id(self, context):
        """Make a remote process call to retrieve the external network id.

        @raise common.RemoteError: with TooManyExternalNetworks
                                   as exc_type if there are
                                   more than one external network
        """
        return self.call(context,
                         self.make_msg('get_external_network_id',
                                       host=self.host),
                         topic=self.topic)

    def report_dead_hosting_devices(self, context, hd_ids=[]):
        """Report that a hosting device cannot be contacted (presumed dead).

        @param: context: contains user information
        @param: kwargs: hosting_device_ids: list of non-responding
                                            hosting devices
        @return: -
        """
        # Cast since we don't expect a return value.
        self.cast(context,
                  self.make_msg('report_non_responding_hosting_devices',
                                host=self.host,
                                hosting_device_ids=hd_ids),
                  topic=self.topic)


class L3NATAgent(manager.Manager):

    """Manager for L3NatAgent.
    This class is based on the reference l3 agent implementation and modified
    for working with configuring hosting devices

    """
    RPC_API_VERSION = '1.1'

    OPTS = [
        cfg.StrOpt('external_network_bridge', default='',
                   help=_("Name of bridge used for external network "
                          "traffic.")),
        cfg.IntOpt('send_arp_for_ha',
                   default=0,
                   help=_("Send this many gratuitous ARPs for HA setup, if "
                          "less than or equal to 0, the feature is disabled")),
        cfg.StrOpt('router_id', default='',
                   help=_("If namespaces is disabled, the l3 agent can only"
                          " confgure a router that has the matching router "
                          "ID.")),
        cfg.BoolOpt('handle_internal_only_routers',
                    default=True,
                    help=_("Agent should implement routers with no gateway")),
        cfg.StrOpt('gateway_external_network_id', default='',
                   help=_("UUID of external network for routers implemented "
                          "by the agents.")),
        cfg.BoolOpt('use_hosting_entities', default=True,
                    help=_("Allow hosting entities for routing service.")),
        cfg.IntOpt('hosting_entity_dead_timeout', default=300,
                   help=_("The time in seconds until a backlogged "
                          "hosting entity is presumed dead ")),
    ]

    def __init__(self, host, conf=None):
        if conf:
            self.conf = conf
        else:
            self.conf = cfg.CONF
        self.router_info = {}
        self.context = context.get_admin_context_without_session()
        self.plugin_rpc = L3PluginApi(topics.PLUGIN, host)
        self.fullsync = True
        self.updated_routers = set()
        self.removed_routers = set()
        self.sync_progress = False
        self._hdm = HostingDevicesManager()
        self.rpc_loop = loopingcall.FixedIntervalLoopingCall(
            self._rpc_loop)
        self.rpc_loop.start(interval=RPC_LOOP_INTERVAL)
        super(L3NATAgent, self).__init__(host=self.conf.host)

    def _fetch_external_net_id(self):
        """Find UUID of single external network for this agent."""
        if self.conf.gateway_external_network_id:
            return self.conf.gateway_external_network_id
        # Cfg agent doesn't use external_network_bridge to handle external
        # networks, so bridge_mappings with provider networks will be used
        # and the cfg agent is able to handle any external networks.
        if not self.conf.external_network_bridge:
            return

        try:
            return self.plugin_rpc.get_external_network_id(self.context)
        except rpc_common.RemoteError as e:
            with excutils.save_and_reraise_exception():
                if e.exc_type == 'TooManyExternalNetworks':
                    msg = _(
                        "The 'gateway_external_network_id' option must be "
                        "configured for this agent as Neutron has more than "
                        "one external network.")
                    raise Exception(msg)

    def _router_added(self, router_id, router):
        ri = RouterInfo(router_id, router)
        driver = self._hdm.get_driver(ri)
        driver.router_added(ri)
        self.router_info[router_id] = ri

    def _router_removed(self, router_id, deconfigure=True):
        ri = self.router_info.get(router_id)
        if ri is None:
            LOG.warn(_("Info for router %s were not found. "
                       "Skipping router removal"), router_id)
            return
        ri.router['gw_port'] = None
        ri.router[l3_constants.INTERFACE_KEY] = []
        ri.router[l3_constants.FLOATINGIP_KEY] = []
        if deconfigure:
            self.process_router(ri)
            driver = self._hdm.get_driver(ri)
            driver.router_removed(ri, deconfigure)
            self._hdm.remove_driver(router_id)
        del self.router_info[router_id]

    def _set_subnet_info(self, port):
        ips = port['fixed_ips']
        if not ips:
            raise Exception(_("Router port %s has no IP address") % port['id'])
        if len(ips) > 1:
            LOG.error(_("Ignoring multiple IPs on router port %s"),
                      port['id'])
        prefixlen = netaddr.IPNetwork(port['subnet']['cidr']).prefixlen
        port['ip_cidr'] = "%s/%s" % (ips[0]['ip_address'], prefixlen)

    def _get_ex_gw_port(self, ri):
        return ri.router.get('gw_port')

    def process_router(self, ri):
        ex_gw_port = self._get_ex_gw_port(ri)
        ri.ha_info = ri.router.get('ha_info', None)
        internal_ports = ri.router.get(l3_constants.INTERFACE_KEY, [])
        existing_port_ids = set([p['id'] for p in ri.internal_ports])
        current_port_ids = set([p['id'] for p in internal_ports
                                if p['admin_state_up']])
        new_ports = [p for p in internal_ports if
                     p['id'] in current_port_ids and
                     p['id'] not in existing_port_ids]
        old_ports = [p for p in ri.internal_ports if
                     p['id'] not in current_port_ids]

        for p in new_ports:
            self._set_subnet_info(p)
            self.internal_network_added(ri, p, ex_gw_port)
            ri.internal_ports.append(p)

        for p in old_ports:
            self.internal_network_removed(ri, p, ex_gw_port)
            ri.internal_ports.remove(p)

        if ex_gw_port and not ri.ex_gw_port:
            self._set_subnet_info(ex_gw_port)
            self.external_gateway_added(ri, ex_gw_port)
        elif not ex_gw_port and ri.ex_gw_port:
            self.external_gateway_removed(ri, ri.ex_gw_port)

        if ex_gw_port:
            self.process_router_floating_ips(ri, ex_gw_port)

        ri.ex_gw_port = ex_gw_port
        self.routes_updated(ri)

    def process_router_floating_ips(self, ri, ex_gw_port):
        floating_ips = ri.router.get(l3_constants.FLOATINGIP_KEY, [])
        existing_floating_ip_ids = set([fip['id'] for fip in ri.floating_ips])
        cur_floating_ip_ids = set([fip['id'] for fip in floating_ips])

        id_to_fip_map = {}

        for fip in floating_ips:
            if fip['port_id']:
                if fip['id'] not in existing_floating_ip_ids:
                    ri.floating_ips.append(fip)
                    self.floating_ip_added(ri, ex_gw_port,
                                           fip['floating_ip_address'],
                                           fip['fixed_ip_address'])

                # store to see if floatingip was remapped
                id_to_fip_map[fip['id']] = fip

        floating_ip_ids_to_remove = (existing_floating_ip_ids -
                                     cur_floating_ip_ids)
        for fip in ri.floating_ips:
            if fip['id'] in floating_ip_ids_to_remove:
                ri.floating_ips.remove(fip)
                self.floating_ip_removed(ri, ri.ex_gw_port,
                                         fip['floating_ip_address'],
                                         fip['fixed_ip_address'])
            else:
                # handle remapping of a floating IP
                new_fip = id_to_fip_map[fip['id']]
                new_fixed_ip = new_fip['fixed_ip_address']
                existing_fixed_ip = fip['fixed_ip_address']
                if (new_fixed_ip and existing_fixed_ip and
                        new_fixed_ip != existing_fixed_ip):
                    floating_ip = fip['floating_ip_address']
                    self.floating_ip_removed(ri, ri.ex_gw_port,
                                             floating_ip, existing_fixed_ip)
                    self.floating_ip_added(ri, ri.ex_gw_port,
                                           floating_ip, new_fixed_ip)
                    ri.floating_ips.remove(fip)
                    ri.floating_ips.append(new_fip)

    def external_gateway_added(self, ri, ex_gw_port):
        driver = self._hdm.get_driver(ri)
        driver.external_gateway_added(ri, ex_gw_port)
        if ri.snat_enabled and len(ri.internal_ports) > 0:
            for port in ri.internal_ports:
                driver.enable_internal_network_NAT(ri, port, ex_gw_port)

    def external_gateway_removed(self, ri, ex_gw_port):
        driver = self._hdm.get_driver(ri)
        if ri.snat_enabled and len(ri.internal_ports) > 0:
            for port in ri.internal_ports:
                driver.disable_internal_network_NAT(ri, port, ex_gw_port)
        driver.external_gateway_removed(ri, ex_gw_port)

    def internal_network_added(self, ri, port, ex_gw_port):
        driver = self._hdm.get_driver(ri)
        driver.internal_network_added(ri, port)
        if ri.snat_enabled and ex_gw_port:
            driver.enable_internal_network_NAT(ri, port, ex_gw_port)

    def internal_network_removed(self, ri, port, ex_gw_port):
        driver = self._hdm.get_driver(ri)
        driver.internal_network_removed(ri, port)
        if ri.snat_enabled and ex_gw_port:
            driver.disable_internal_network_NAT(ri, port, ex_gw_port)

    def floating_ip_added(self, ri, ex_gw_port, floating_ip, fixed_ip):
        driver = self._hdm.get_driver(ri)
        driver.floating_ip_added(ri, ex_gw_port, floating_ip, fixed_ip)

    def floating_ip_removed(self, ri, ex_gw_port, floating_ip, fixed_ip):
        driver = self._hdm.get_driver(ri)
        driver.floating_ip_removed(ri, ex_gw_port, floating_ip, fixed_ip)

    def router_deleted(self, context, router_id):
        """Deal with router deletion RPC message."""
        LOG.debug(_('Got router deleted notification for %s'), router_id)
        self.removed_routers.add(router_id)

    def routers_updated(self, context, routers):
        """Deal with routers modification and creation RPC message."""
        LOG.debug(_('Got routers updated notification :%s'), routers)
        if routers:
            # This is needed for backward compatibility
            if isinstance(routers[0], dict):
                routers = [router['id'] for router in routers]
            self.updated_routers.update(routers)

    def hosting_entity_removed(self, context, payload):
        """ RPC Notification that a hosting entity was removed.
        Expected Payload format:
        {
             'hosting_data': {'he_id1': {'routers': [id1, id2, ...]},
                              'he_id2': {'routers': [id3, id4, ...]}, ... },
             'deconfigure': True/False
        }
        """
        for he_id, resource_data in payload['hosting_data'].items():
            LOG.debug(_("Hosting entity removal data: %s "),
                      payload['hosting_data'])
            for router_id in resource_data.get('routers', []):
                self._router_removed(router_id, payload['deconfigure'])
            self._hdm.pop(he_id)

    def router_removed_from_agent(self, context, payload):
        LOG.debug(_('Got router removed from agent :%r'), payload)
        self.removed_routers.add(payload['router_id'])

    def router_added_to_agent(self, context, payload):
        LOG.debug(_('Got router added to agent :%r'), payload)
        self.routers_updated(context, payload)

    def _process_routers(self, routers, all_routers=False):
        pool = eventlet.GreenPool()
        if (self.conf.external_network_bridge and
                not (ip_lib.device_exists(self.conf.external_network_bridge))):
            LOG.error(_("The external network bridge '%s' does not exist"),
                      self.conf.external_network_bridge)
            return

        target_ex_net_id = self._fetch_external_net_id()
        # if routers are all the routers we have (They are from router sync on
        # starting or when error occurs during running), we seek the
        # routers which should be removed.
        # If routers are from server side notification, we seek them
        # from subset of incoming routers and ones we have now.
        if all_routers:
            prev_router_ids = set(self.router_info)
        else:
            prev_router_ids = set(self.router_info) & set(
                [router['id'] for router in routers])
        cur_router_ids = set()
        for r in routers:
            if not r['admin_state_up']:
                continue
            # Note: Whether the router is assigned to a dedicated hosting
            # device is decided and enforced by the plugin.
            # So no checks are done here.
            ex_net_id = (r['external_gateway_info'] or {}).get('network_id')
            if not ex_net_id and not self.conf.handle_internal_only_routers:
                continue
            if (target_ex_net_id and ex_net_id and
                    ex_net_id != target_ex_net_id):
                continue
            cur_router_ids.add(r['id'])
            if not self._hdm.is_hosting_entity_reachable(r['id'], r):
                LOG.info(_("Router: %(id)s is on unreachable hosting entity. "
                         "Skip processing it."), {'id': r['id']})
                continue
            if r['id'] not in self.router_info:
                self._router_added(r['id'], r)
            ri = self.router_info[r['id']]
            ri.router = r
            pool.spawn_n(self.process_router, ri)
        # identify and remove routers that no longer exist
        for router_id in prev_router_ids - cur_router_ids:
            pool.spawn_n(self._router_removed, router_id)
            pool.waitall()

    @lockutils.synchronized('l3-agent', 'neutron-')
    def _rpc_loop(self):
        # _rpc_loop and _sync_routers_task will not be
        # executed in the same time because of lock.
        # so we can clear the value of updated_routers
        # and removed_routers
        try:
            LOG.debug(_("Starting RPC loop for %d updated routers"),
                      len(self.updated_routers))
            if self.updated_routers:
                router_ids = list(self.updated_routers)
                self.updated_routers.clear()
                routers = self.plugin_rpc.get_routers(
                    self.context, router_ids)
                self._process_routers(routers)
            self._process_router_delete()
            LOG.debug(_("RPC loop successfully completed"))
        except Exception:
            LOG.exception(_("Failed synchronizing routers"))
            self.fullsync = True

    def _process_router_delete(self):
        current_removed_routers = list(self.removed_routers)
        for router_id in current_removed_routers:
            self._router_removed(router_id)
            self.removed_routers.remove(router_id)

    def _router_ids(self):
        if not self.conf.use_namespaces:
            return [self.conf.router_id]

    @periodic_task.periodic_task
    @lockutils.synchronized('l3-agent', 'neutron-')
    def _sync_routers_task(self, context):
        LOG.debug(_("Starting _sync_routers_task - fullsync:%s"),
                  self.fullsync)
        if not self.fullsync:
            return
        try:
            router_ids = self._router_ids()
            self.updated_routers.clear()
            self.removed_routers.clear()
            routers = self.plugin_rpc.get_routers(
                context, router_ids)

            LOG.debug(_('Processing :%r'), routers)
            self._process_routers(routers, all_routers=True)
            self.fullsync = False
            LOG.debug(_("_sync_routers_task successfully completed"))
        except Exception:
            LOG.exception(_("Failed synchronizing routers"))
            self.fullsync = True
        else:
            LOG.debug(_("Full sync is False. Processing backlog."))
            res = self._hdm.check_backlogged_hosting_entities()
            if res['reachable']:
                #Fetch routers for this reachable Hosting Device
                LOG.debug(_("Requesting routers for hosting entities: %s "
                            "that are now responding."), res['reachable'])
                routers = self.plugin_rpc.get_routers(
                    context, router_ids=None, he_ids=res['reachable'])
                self._process_routers(routers, all_routers=True)
            if res['dead']:
                LOG.debug(_("Reporting dead hosting entities: %s"),
                          res['dead'])
                # Process dead Hosting Device
                self.plugin_rpc.report_dead_hosting_entities(
                    context, he_ids=res['dead'])

    def after_start(self):
        LOG.info(_("L3 Cfg Agent started"))

    def routes_updated(self, ri):
        new_routes = ri.router['routes']
        old_routes = ri.routes
        adds, removes = common_utils.diff_list_of_dict(old_routes,
                                                       new_routes)
        for route in adds:
            LOG.debug(_("Added route entry is '%s'"), route)
            # remove replaced route from deleted route
            for del_route in removes:
                if route['destination'] == del_route['destination']:
                    removes.remove(del_route)
            #replace success even if there is no existing route
            driver = self._hdm.get_driver(ri)
            driver.routes_updated(ri, 'replace', route)

        for route in removes:
            LOG.debug(_("Removed route entry is '%s'"), route)
            driver = self._hdm.get_driver(ri)
            driver.routes_updated(ri, 'delete', route)
        ri.routes = new_routes


class L3NATAgentWithStateReport(L3NATAgent):

    def __init__(self, host, conf=None):
        super(L3NATAgentWithStateReport, self).__init__(host=host, conf=conf)
        self.state_rpc = agent_rpc.PluginReportStateAPI(topics.PLUGIN)
        self.agent_state = {
            'binary': 'neutron-l3-cfg-agent',
            'host': host,
            'topic': cl3_constants.CFG_AGENT,
            'configurations': {
                # 'use_namespaces': self.conf.use_namespaces,
                # 'router_id': self.conf.router_id,
                # 'handle_internal_only_routers':
                # self.conf.handle_internal_only_routers,
                # 'gateway_external_network_id':
                # self.conf.gateway_external_network_id,
                # 'interface_driver': self.conf.interface_driver,
                'hosting_entity_drivers': {
                    cl3_constants.CSR1KV_HOST:
                    'neutron.plugins.cisco.l3.agent.csr1000v.'
                    'cisco_csr_network_driver.CSR1000vRoutingDriver'}},
            'start_flag': True,
            'agent_type': cl3_constants.AGENT_TYPE_CFG}
        report_interval = cfg.CONF.AGENT.report_interval
        self.use_call = True
        if report_interval:
            self.heartbeat = loopingcall.FixedIntervalLoopingCall(
                self._report_state)
            self.heartbeat.start(interval=report_interval)

    def _report_state(self):
        LOG.debug(_("Report state task started"))
        num_ex_gw_ports = 0
        num_interfaces = 0
        num_floating_ips = 0
        router_infos = self.router_info.values()
        num_routers = len(router_infos)
        num_he_routers = {}
        for ri in router_infos:
            ex_gw_port = self._get_ex_gw_port(ri)
            if ex_gw_port:
                num_ex_gw_ports += 1
            num_interfaces += len(ri.router.get(l3_constants.INTERFACE_KEY,
                                                []))
            num_floating_ips += len(ri.router.get(l3_constants.FLOATINGIP_KEY,
                                                  []))
            he = ri.router['hosting_device']
            if he:
                num_he_routers[he['id']] = num_he_routers.get(he['id'], 0) + 1
        routers_per_he = {he_id: {'routers': num} for he_id, num
                          in num_he_routers.items()}
        non_responding = self._hdm.get_backlogged_hosting_entities()
        configurations = self.agent_state['configurations']
        configurations['total routers'] = num_routers
        configurations['total ex_gw_ports'] = num_ex_gw_ports
        configurations['total interfaces'] = num_interfaces
        configurations['total floating_ips'] = num_floating_ips
        configurations['hosting_entities'] = routers_per_he
        configurations['non_responding_hosting_entities'] = non_responding
        try:
            self.state_rpc.report_state(self.context, self.agent_state,
                                        self.use_call)
            self.agent_state.pop('start_flag', None)
            self.use_call = False
            LOG.debug(_("Report state task successfully completed"))
        except AttributeError:
            # This means the server does not support report_state
            LOG.warn(_("Neutron server does not support state report."
                       " State report for this agent will be disabled."))
            self.heartbeat.stop()
            return
        except Exception:
            LOG.exception(_("Failed reporting state!"))

    def agent_updated(self, context, payload):
        """Handle the agent_updated notification event."""
        self.fullsync = True
        LOG.info(_("agent_updated by server side %s!"), payload)


def main(manager='neutron.plugins.cisco.l3.agent.'
                 'cfg_agent.L3NATAgentWithStateReport'):
    #eventlet.monkey_patch()
    conf = cfg.CONF
    conf.register_opts(L3NATAgent.OPTS)
    config.register_agent_state_opts_helper(conf)
    config.register_root_helper(conf)
    conf.register_opts(interface.OPTS)
    conf.register_opts(external_process.OPTS)
    conf(project='neutron')
    config.setup_logging(conf)
    server = neutron_service.Service.create(
        binary='neutron-l3-cfg-agent',
        topic=cl3_constants.CFG_AGENT,
        report_interval=cfg.CONF.AGENT.report_interval,
        manager=manager)
    service.launch(server).wait()
