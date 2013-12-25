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
# @author: Bob Melander, Cisco Systems, Inc.

import copy

from oslo.config import cfg
from sqlalchemy.orm import exc
from sqlalchemy.orm import joinedload

from neutron import context as n_context
from neutron.common import constants as l3_constants
from neutron.common import exceptions as n_exc
from neutron.db import extraroute_db
from neutron.db import l3_db
from neutron.db import models_v2
from neutron.extensions import providernet as pr_net
from neutron import manager
from neutron.openstack.common import lockutils
from neutron.openstack.common import log as logging
from neutron.openstack.common import loopingcall
from neutron.plugins.cisco.l3.common import l3_rpc_joint_agent_api
from neutron.plugins.cisco.l3.common import constants as cl3_const
from neutron.plugins.cisco.l3.db import hosting_device_manager_db
from neutron.plugins.cisco.l3.db.l3_models import RouterHostingDeviceBinding
from neutron.plugins.cisco.l3.db.l3_models import HostedHostingPortBinding

LOG = logging.getLogger(__name__)


ROUTER_APPLIANCE_OPTS = [
    cfg.StrOpt('default_router_type', default='CSR1kv',
               help=_("Default type of router to create")),
    cfg.StrOpt('hosting_scheduler_driver',
               default='neutron.plugins.cisco.l3.scheduler.'
                       'l3_hosting_device_scheduler.L3HostingDeviceScheduler',
               help=_('Driver to use for scheduling router to a hosting '
                      'entity')),
    cfg.StrOpt('backlog_processing_interval',
               default=10,
               help=_('Time in seconds between renewed scheduling attempts of '
                      'non-scheduled routers')),
]

cfg.CONF.register_opts(ROUTER_APPLIANCE_OPTS)


class RouterCreateInternalError(n_exc.NeutronException):
    message = _("Router could not be created due to internal error.")


class RouterInternalError(n_exc.NeutronException):
    message = _("Internal error during router processing.")


class RouterBindingInfoError(n_exc.NeutronException):
    message = _("Could not get binding information for router %(router_id)s.")


class L3_router_appliance_db_mixin(extraroute_db.ExtraRoute_db_mixin):
    """ Mixin class to support router appliances to implement Neutron's
        L3 routing functionality """

    hosting_scheduler = None

    # Dict of routers for which new scheduling attempts
    # should be made and the heartbeat for that.
    _backlogged_routers = {}
    _refresh_router_backlog = True
    _heartbeat = None

    @property
    def _core_plugin(self):
        return manager.NeutronManager.get_plugin()

    @property
    def _dev_mgr(self):
        return hosting_device_manager_db.HostingDeviceManager.get_instance()

    def create_router(self, context, router):
#        self._dev_mgr.delete_all_service_vm_hosting_devices(
#           context.elevated(), cl3_const.CSR1KV_HOST)
#        return router
        r = router['router']
        # Bob: Hard coding router type to shared CSR1kv for now
        r['router_type'] = cfg.CONF.default_router_type
        r['share_host'] = True
        if (r['router_type'] != cl3_const.NAMESPACE_ROUTER_TYPE and
                self._dev_mgr.mgmt_nw_id() is None):
            raise RouterCreateInternalError()
        with context.session.begin(subtransactions=True):
            router_created = (super(L3_router_appliance_db_mixin, self).
                              create_router(context, router))
            r_hd_b_db = RouterHostingDeviceBinding(
                router_id=router_created['id'],
                router_type=r.get('router_type',
                                  cfg.CONF.default_router_type),
                auto_schedule=r.get('auto_schedule',
                                    cfg.CONF.router_auto_schedule),
                share_hosting_device=r.get('share_host', True),
                hosting_device_id=None)
            context.session.add(r_hd_b_db)
        return router_created

    def update_router(self, context, id, router):
        r = router['router']
        # Check if external gateway has changed so we may have to
        # update trunking
        o_r_db = self._get_router(context, id)
        old_ext_gw = (o_r_db.gw_port or {}).get('network_id')
        new_ext_gw = r.get('external_gateway_info', {}).get('network_id')
        with context.session.begin(subtransactions=True):
            if old_ext_gw is not None and old_ext_gw != new_ext_gw:
                o_r = self._make_router_dict(o_r_db, process_extensions=False)
                # no need to schedule now since we're only doing this to
                # tear-down connectivity and there won't be any if not
                # already scheduled.
                self._add_type_and_hosting_device_info(context, o_r,
                                                       schedule=False)
                host_type = (o_r['hosting_device'] or {}).get('host_type')
                p_drv = self._dev_mgr.get_hosting_device_plugging_driver(
                    context, host_type)
                if p_drv is not None:
                    p_drv.teardown_logical_port_connectivity(context,
                                                             o_r_db.gw_port)
            router_updated = (
                super(L3_router_appliance_db_mixin, self).update_router(
                    context, id, router))
            routers = [copy.deepcopy(router_updated)]
            self._add_type_and_hosting_device_info(context, routers[0])
        l3_rpc_joint_agent_api.L3JointAgentNotify.routers_updated(
            context, routers)
        return router_updated

    def delete_router(self, context, id):
        router_db = self._get_router(context, id)
        router = self._make_router_dict(router_db)
        with context.session.begin(subtransactions=True):
            self._add_type_and_hosting_device_info(context, router,
                                                   schedule=False)
            if router_db.gw_port is not None:
                host_type = (router['hosting_device'] or {}).get('host_type')
                p_drv = self._dev_mgr.get_hosting_device_plugging_driver(
                    context, host_type)
                if p_drv is not None:
                    p_drv.teardown_logical_port_connectivity(context,
                                                             router_db.gw_port)
            # conditionally remove router from backlog just to be sure
            self.remove_router_from_backlog('id')
            super(L3_router_appliance_db_mixin, self).delete_router(context,
                                                                    id)
        l3_rpc_joint_agent_api.L3JointAgentNotify.router_deleted(context,
                                                                 router)

    def add_router_interface(self, context, router_id, interface_info):
        with context.session.begin(subtransactions=True):
            info = (super(L3_router_appliance_db_mixin, self).
                    add_router_interface(context, router_id, interface_info))
            routers = [self.get_router(context, router_id)]
            self._add_type_and_hosting_device_info(context, routers[0])
        l3_rpc_joint_agent_api.L3JointAgentNotify.routers_updated(
            context, routers, 'add_router_interface')
        return info

    def remove_router_interface(self, context, router_id, interface_info):
        if 'port_id' in (interface_info or {}):
            port_db = self._core_plugin._get_port(
                context, interface_info['port_id'])
        elif 'subnet_id' in (interface_info or {}):
            subnet_db = self._core_plugin._get_subnet(
                context, interface_info['subnet_id'])
            port_db = self._get_router_port_db_on_subnet(
                context, router_id, subnet_db)
        else:
            msg = "Either subnet_id or port_id must be specified"
            raise n_exc.BadRequest(resource='router', msg=msg)
        routers = [self.get_router(context, router_id)]
        with context.session.begin(subtransactions=True):
            self._add_type_and_hosting_device_info(context, routers[0])
            host_type = (routers[0]['hosting_device'] or {}).get('host_type')
            p_drv = self._dev_mgr.get_hosting_device_plugging_driver(
                context, host_type)
            if p_drv is not None:
                p_drv.teardown_logical_port_connectivity(context, port_db)
            info = (super(L3_router_appliance_db_mixin, self).
                    remove_router_interface(context, router_id,
                                            interface_info))
        l3_rpc_joint_agent_api.L3JointAgentNotify.routers_updated(
            context, routers, 'remove_router_interface')
        return info

    def create_floatingip(self, context, floatingip):
        with context.session.begin(subtransactions=True):
            info = super(L3_router_appliance_db_mixin, self).create_floatingip(
                context, floatingip)
            if info['router_id']:
                routers = [self.get_router(context, info['router_id'])]
                self._add_type_and_hosting_device_info(context, routers[0])
                l3_rpc_joint_agent_api.L3JointAgentNotify.routers_updated(
                    context, routers, 'create_floatingip')
        return info

    def update_floatingip(self, context, id, floatingip):
        orig_fl_ip = super(L3_router_appliance_db_mixin, self).get_floatingip(
            context, id)
        before_router_id = orig_fl_ip['router_id']
        with context.session.begin(subtransactions=True):
            info = super(L3_router_appliance_db_mixin, self).update_floatingip(
                context, id, floatingip)
            router_ids = []
            if before_router_id:
                router_ids.append(before_router_id)
            router_id = info['router_id']
            if router_id and router_id != before_router_id:
                router_ids.append(router_id)
            routers = []
            for router_id in router_ids:
                router = self.get_router(context, router_id)
                self._add_type_and_hosting_device_info(context, router)
                routers.append(router)
        l3_rpc_joint_agent_api.L3JointAgentNotify.routers_updated(
            context, routers, 'update_floatingip')
        return info

    def delete_floatingip(self, context, id):
        floatingip_db = self._get_floatingip(context, id)
        router_id = floatingip_db['router_id']
        with context.session.begin(subtransactions=True):
            super(L3_router_appliance_db_mixin, self).delete_floatingip(
                context, id)
            if router_id:
                routers = [self.get_router(context, router_id)]
                self._add_type_and_hosting_device_info(context, routers[0])
                l3_rpc_joint_agent_api.L3JointAgentNotify.routers_updated(
                    context, routers, 'delete_floatingip')

    def disassociate_floatingips(self, context, port_id):
        with context.session.begin(subtransactions=True):
            try:
                fip_qry = context.session.query(l3_db.FloatingIP)
                floating_ip = fip_qry.filter_by(fixed_port_id=port_id).one()
                router_id = floating_ip['router_id']
                floating_ip.update({'fixed_port_id': None,
                                    'fixed_ip_address': None,
                                    'router_id': None})
            except exc.NoResultFound:
                return
            except exc.MultipleResultsFound:
                # should never happen
                raise Exception(_('Multiple floating IPs found for port %s')
                                % port_id)
            if router_id:
                routers = [self.get_router(context, router_id)]
                self._add_type_and_hosting_device_info(context, routers[0])
                l3_rpc_joint_agent_api.L3JointAgentNotify.routers_updated(
                    context, routers)

    def handle_non_responding_hosting_devices(self, context, cfg_agent,
                                              hosting_device_ids):
        hosting_devices = self._dev_mgr.get_hosting_devices(context.elevated(),
                                                            hosting_device_ids)
        # Information to send to Cisco cfg agent:
        #    {'hd_id1': {'routers': [id1, id2, ...]},
        #     'hd_id2': {'routers': [id3, id4, ...]}, ...}
        hosting_info = {}
        to_reschedule = []
        for hd in hosting_devices:
            hd_id = hd['id']
            hd_bindings = self._get_hosting_device_bindings(context, hd_id)
            router_ids = []
            for binding in hd_bindings:
                router_ids.append(binding['router_id'])
                if binding['auto_schedule']:
                    to_reschedule.append(binding['router_id'])
            logical_resource_ids = {'routers': router_ids}
            was_deleted = self._dev_mgr.process_non_responsive_hosting_device(
                context.elevated(), hd, logical_resource_ids)
            if was_deleted:
                hosting_info[hd_id] = logical_resource_ids
        l3_rpc_joint_agent_api.L3JointAgentNotify.hosting_device_removed(
            context, hosting_info, False, cfg_agent)
        if to_reschedule:
            # Reschedule routers that should be auto-scheduled
            routers = self.get_sync_data_ext(context, to_reschedule)
            l3_rpc_joint_agent_api.L3JointAgentNotify.routers_updated(context,
                                                                      routers)

    # Make parent's call to get_sync_data(...) a noop
    def get_sync_data(self, context, router_ids=None, active=None):
        return []

    def get_sync_data_ext(self, context, router_ids=None, active=None,
                          ext_gw_change_status=None,
                          int_if_change_status=None):
        """Query routers and their related floating_ips, interfaces.
        Adds information about hosting device as well as trunking.
        """
        with context.session.begin(subtransactions=True):
            sync_data = (super(L3_router_appliance_db_mixin, self).
                         get_sync_data(context, router_ids, active))
            for router in sync_data:
                self._add_type_and_hosting_device_info(context, router)
                host_type = (router.get('hosting_device') or {}).get('host_type')
                if (host_type != cl3_const.NETWORK_NODE_HOST and
                        host_type is not None):
                    plg_drv = self._dev_mgr.get_hosting_device_plugging_driver(
                        context, host_type)
                    if plg_drv is not None:
                        self._add_hosting_port_info(context, router, plg_drv)
        return sync_data

    @lockutils.synchronized('routers', 'neutron-')
    def backlog_router(self, router):
        if ((router or {}).get('id') is None or
                router['id'] in self._backlogged_routers):
            return
        self._backlogged_routers[router['id']] = router

    @lockutils.synchronized('routers', 'neutron-')
    def remove_router_from_backlog(self, id):
        self._backlogged_routers.pop(id, None)

    @lockutils.synchronized('routerbacklog', 'neutron-')
    def _process_backlogged_routers(self):
        if self._refresh_router_backlog:
            self._sync_router_backlog()
        if not self._backlogged_routers:
            return
        context = n_context.get_admin_context()
        scheduled_routers = []
        # try to reschedule
        for r_id, router in self._backlogged_routers.items():
            self._add_type_and_hosting_device_info(context, router)
            if router['hosting_device']:
                # scheduling attempt succeeded
                scheduled_routers.append(router)
                self._backlogged_routers.pop(r_id, None)
        # notify cfg agents so the scheduled routers are instantiated
        if scheduled_routers:
            l3_rpc_joint_agent_api.L3JointAgentNotify.routers_updated(
                context, scheduled_routers)

    def _setup_backlog_handling(self):
        self._heartbeat = loopingcall.FixedIntervalLoopingCall(
            self._process_backlogged_routers)
        self._heartbeat.start(interval=cfg.CONF.backlog_processing_interval)

    def _sync_router_backlog(self):
        context = n_context.get_admin_context()
        type_to_exclude = cl3_const.NAMESPACE_ROUTER_TYPE
        query = context.session.query(RouterHostingDeviceBinding)
        query = query.options(joinedload('router'))
        query = query.filter(
            RouterHostingDeviceBinding.router_type != type_to_exclude,
            RouterHostingDeviceBinding.hosting_device_id == None)
        for binding in query:
            router = self._make_router_dict(binding.router,
                                            process_extensions=False)
            self._backlogged_routers[binding.router_id] = router
        self._refresh_router_backlog = False

    def host_router(self, context, router_id):
        """Schedules non-hosted auto-schedulable router(s) on hosting devices.
        If <router_id> is given, then only the router with that id is
        scheduled (if it is non-hosted). If no <router_id> is given,
        then all non-hosted routers are scheduled.
        """
        if self.hosting_scheduler is None:
            return
        query = context.session.query(RouterHostingDeviceBinding)
        query = query.filter(
            RouterHostingDeviceBinding.router_type !=
            cl3_const.NAMESPACE_ROUTER_TYPE,
            RouterHostingDeviceBinding.auto_schedule == True,
            RouterHostingDeviceBinding.hosting_device == None)
        if router_id:
            query = query.filter(
                RouterHostingDeviceBinding.router_id == router_id)
        for r_hd_binding in query:
            router = self._make_router_dict(r_hd_binding.router)
            router['router_type'] = r_hd_binding['router_type']
            router['share_host'] = r_hd_binding['share_hosting_device']
            self.hosting_scheduler.schedule_router_on_hosting_device(
                self, context, router, r_hd_binding)

    def _get_router_binding_info(self, context, id, load_hd_info=True):
        query = context.session.query(RouterHostingDeviceBinding)
        if load_hd_info:
            query = query.options(joinedload('hosting_device'))
        query = query.filter(RouterHostingDeviceBinding.router_id == id)
        try:
            r_hd_b = query.one()
            return r_hd_b
        except exc.NoResultFound:
            # This should not happen
            LOG.error(_('DB inconsistency: No type and hosting info associated'
                        ' with router %s'), id)
            raise RouterBindingInfoError(router_id=id)
        except exc.MultipleResultsFound:
            # This should not happen either
            LOG.error(_('DB inconsistency: Multiple type and hosting info'
                        ' associated with router %s'), id)
            raise RouterBindingInfoError(router_id=id)

    def _get_hosting_device_bindings(self, context, id, load_routers=False,
                                    load_hosting_device=False):
        query = context.session.query(RouterHostingDeviceBinding)
        if load_routers:
            query = query.options(joinedload('router'))
        if load_hosting_device:
            query = query.options(joinedload('hosting_device'))
        query = query.filter(
            RouterHostingDeviceBinding.hosting_device_id == id)
        return query.all()

    def _add_type_and_hosting_device_info(self, context, router,
                                          binding_info=None, schedule=True):
        """Adds type and hosting device information to a router."""
        try:
            if binding_info is None:
                binding_info = self._get_router_binding_info(context,
                                                             router['id'])
        except RouterBindingInfoError:
            return
        router['router_type'] = binding_info['router_type']
        router['share_host'] = binding_info['share_hosting_device']
        if binding_info.router_type == cl3_const.NAMESPACE_ROUTER_TYPE:
            router['hosting_device'] = None
            return
        if binding_info.hosting_device is None and schedule:
            # This router has not been scheduled to a hosting device
            # so we try to do it now.
            self.hosting_scheduler.schedule_router_on_hosting_device(
                self, context, router, binding_info)
            context.session.expire(binding_info)
        if binding_info.hosting_device is None:
            router['hosting_device'] = None
        else:
            router['hosting_device'] = {
                'id': binding_info.hosting_device.id,
                'host_type': binding_info.hosting_device.host_type,
                'ip_address': binding_info.hosting_device.ip_address,
                'port': binding_info.hosting_device.transport_port,
                'created_at': str(binding_info.hosting_device.created_at),
                'booting_time': binding_info.hosting_device.booting_time}

    def _add_hosting_port_info(self, context, router, plugging_driver):
        """Adds hosting port information to router ports.
        """
        # We only populate hosting port info, i.e., reach here, if the
        # router has been scheduled to a hosting device. Hence this
        # a good place to allocate hosting ports to the router ports.
        # cache of hosting port information: {mac_addr: {'name': port_name}}
        hosting_pdata = {}
        if router['external_gateway_info'] is not None:
            h_info, did_allocation = self._populate_hosting_info_for_port(
                context, router['id'], router['gw_port'], router['hosting_device'],
                hosting_pdata, plugging_driver)
        for itfc in router.get(l3_constants.INTERFACE_KEY, []):
            h_info, did_allocation = self._populate_hosting_info_for_port(
                context, router['id'], itfc, router['hosting_device'],
                hosting_pdata, plugging_driver)

    def _populate_hosting_info_for_port(self, context, router_id, port,
                                        hosting_device, hosting_pdata,
                                        plugging_driver):
        port_db = self._core_plugin._get_port(context, port['id'])
        h_info = port_db.hosting_info
        new_allocation = False
        if h_info is None:
            # The port does not yet have a hosting port so allocate one now
            h_info = self._allocate_hosting_port(
                context, router_id, port_db, hosting_device['id'], plugging_driver)
            if h_info is None:
                # This should not happen but just in case ...
                LOG.error(_('Failed to allocate hosting port for port %s'),
                          port['id'])
                port['hosting_info'] = None
                return None, new_allocation
            else:
                new_allocation = True
        if hosting_pdata.get('mac') is None:
            p_data = self._core_plugin.get_port(
                context, h_info.hosting_port_id, ['mac_address', 'name'])
            hosting_pdata['mac'] = p_data['mac_address']
            hosting_pdata['name'] = p_data['name']
        # Including MAC address of hosting port so L3CfgAgent can easily
        # determine which VM VIF to configure VLAN sub-interface on.
        port['hosting_info'] = {'hosting_port_id': h_info.hosting_port_id,
                                'hosting_mac': hosting_pdata.get('mac'),
                                'hosting_port_name': hosting_pdata.get('name')}
        plugging_driver.extend_hosting_port_info(
            context, port_db, port['hosting_info'])
        return h_info, new_allocation

    def _allocate_hosting_port(self, context, router_id, port_db,
                               hosting_device_id, plugging_driver):
        net_data = self._core_plugin.get_network(
            context, port_db['network_id'], [pr_net.NETWORK_TYPE])
        network_type = net_data.get(pr_net.NETWORK_TYPE)
        alloc = plugging_driver.allocate_hosting_port(
            context, router_id, port_db, network_type, hosting_device_id)
        if alloc is None:
            return
        with context.session.begin(subtransactions=True):
            h_info = HostedHostingPortBinding(
                router_id=router_id,
                router_port_id=port_db['id'],
                network_type=network_type,
                hosting_port_id=alloc['allocated_port_id'],
                segmentation_tag=alloc['allocated_vlan'])
            context.session.add(h_info)
            context.session.expire(port_db)
        # allocation succeeded so establish connectivity for logical port
        context.session.expire(h_info)
        plugging_driver.setup_logical_port_connectivity(context, port_db)
        return h_info

    def _get_router_port_db_on_subnet(self, context, router_id, subnet):
        try:
            rport_qry = context.session.query(models_v2.Port)
            ports = rport_qry.filter_by(
                device_id=router_id,
                device_owner=l3_db.DEVICE_OWNER_ROUTER_INTF,
                network_id=subnet['network_id'])
            for p in ports:
                if p['fixed_ips'][0]['subnet_id'] == subnet['id']:
                    return p
        except exc.NoResultFound:
            return


