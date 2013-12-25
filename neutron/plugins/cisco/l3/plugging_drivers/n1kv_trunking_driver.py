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

import eventlet

from oslo.config import cfg
from sqlalchemy import and_
from sqlalchemy.orm import exc

from neutron.api.v2 import attributes
from neutron.common import exceptions as n_exc
from neutron import context as n_context
from neutron.db import models_v2
from neutron.extensions import providernet as pr_net
from neutron import manager
from neutron.openstack.common import log as logging
from neutron.plugins.cisco.extensions import n1kv_profile
from neutron.plugins.cisco.l3.common import n1kv_constants as n1kv_const
from neutron.plugins.cisco.l3.db.l3_router_appliance_db import HostedHostingPortBinding
import neutron.plugins.cisco.l3.plugging_drivers as plug
from neutron.plugins.cisco.l3.db import hosting_device_manager_db

LOG = logging.getLogger(__name__)


N1KV_TRUNKING_DRIVER_OPTS = [
    cfg.StrOpt('management_port_profile', default='osn_mgmt_pp',
               help=_("Name of N1kv port profile for management ports")),
    cfg.StrOpt('t1_port_profile', default='osn_t1_pp',
               help=_("Name of N1kv port profile for T1 ports")),
    cfg.StrOpt('t2_port_profile', default='osn_t2_pp',
               help=_("Name of N1kv port profile for T2 ports")),
    cfg.StrOpt('t1_network_profile', default='osn_t1_np',
               help=_("Name of N1kv network profile for T1 networks")),
    cfg.StrOpt('t2_network_profile', default='osn_t2_np',
               help=_("Name of N1kv network profile for T2 networks")),
]

cfg.CONF.register_opts(N1KV_TRUNKING_DRIVER_OPTS)

MIN_LL_VLAN_TAG = 10
MAX_LL_VLAN_TAG = 200
FULL_VLAN_SET = set(range(MIN_LL_VLAN_TAG, MAX_LL_VLAN_TAG))

# Port lookups can fail so retries are needed
MAX_HOSTING_PORT_LOOKUP_ATTEMPTS = 10
SECONDS_BETWEEN_HOSTING_PORT_LOOKSUPS = 2


class N1kvTrunkingPlugDriver(plug.PluginSidePluggingDriver):
    """This is a driver class for service VMs used together with
    the N1kv plugin. It makes use that plugin's VLAN trunk feature.
    """
    _n1kv_mgmt_pp_id = None
    _n1kv_t1_pp_id = None
    _n1kv_t2_pp_id = None
    _n1kv_t1_np_id = None
    _n1kv_t2_np_id = None

    @property
    def _core_plugin(self):
        return manager.NeutronManager.get_plugin()

    @property
    def _dev_mgr(self):
        return hosting_device_manager_db.HostingDeviceManager.get_instance()

    @classmethod
    def _get_profile_id(cls, p_type, resource, name):
        try:
            tenant_id = (hosting_device_manager_db.HostingDeviceManager.
                         get_instance().l3_tenant_id())
        except AttributeError:
            return
        if tenant_id is None:
            return
        core_plugin = manager.NeutronManager.get_plugin()
        if p_type == 'net_profile':
            profiles = core_plugin.get_network_profiles(
                n_context.get_admin_context(),
                {'tenant_id': [tenant_id], 'name': [name]},
                ['id'])
        else:
            profiles = core_plugin.get_policy_profiles(
                n_context.get_admin_context(),
                {'tenant_id': [tenant_id], 'name': [name]},
                ['id'])
        if len(profiles) == 1:
            return profiles[0]['id']
        elif len(profiles) > 1:
            # Profile must have a unique name.
            LOG.error(_('The %(resource)s %(name)s does not have unique name. '
                        'Please refer to admin guide and create one.'),
                      {'resource': resource, 'name': name})
        else:
            # Profile has not been created.
            LOG.error(_('There is no %(resource)s %(name)s. Please refer to '
                        'admin guide and create one.'),
                      {'resource': resource, 'name': name})

    @classmethod
    def n1kv_mgmt_pp_id(cls):
        if cls._n1kv_mgmt_pp_id is None:
            cls._n1kv_mgmt_pp_id = cls._get_profile_id(
                'port_profile', 'N1kv port profile',
                cfg.CONF.management_port_profile)
        return cls._n1kv_mgmt_pp_id

    @classmethod
    def n1kv_t1_pp_id(cls):
        if cls._n1kv_t1_pp_id is None:
            cls._n1kv_t1_pp_id = cls._get_profile_id(
                'port_profile', 'N1kv port profile', cfg.CONF.t1_port_profile)
        return cls._n1kv_t1_pp_id

    @classmethod
    def n1kv_t2_pp_id(cls):
        if cls._n1kv_t2_pp_id is None:
            cls._n1kv_t2_pp_id = cls._get_profile_id(
                'port_profile', 'N1kv port profile', cfg.CONF.t2_port_profile)
        return cls._n1kv_t2_pp_id

    @classmethod
    def n1kv_t1_np_id(cls):
        if cls._n1kv_t1_np_id is None:
            cls._n1kv_t1_np_id = cls._get_profile_id(
                'net_profile', 'N1kv network profile',
                cfg.CONF.t1_network_profile)
        return cls._n1kv_t1_np_id

    @classmethod
    def n1kv_t2_np_id(cls):
        if cls._n1kv_t2_np_id is None:
            cls._n1kv_t2_np_id = cls._get_profile_id(
                'net_profile', 'N1kv network profile',
                cfg.CONF.t2_network_profile)
        return cls._n1kv_t2_np_id

    def create_hosting_device_resources(self, context, tenant_id, mgmt_nw_id,
                                        mgmt_sec_grp_id, max_hosted, **kwargs):
        mgmt_port = None
        t1_n, t1_sn, t2_n, t2_sn, t_p = [], [], [], [], []
        if mgmt_nw_id is not None and tenant_id is not None:
            # Create port for mgmt interface
            p_spec = {'port': {
                'tenant_id': tenant_id,
                'admin_state_up': True,
                'name': 'mgmt',
                'network_id': mgmt_nw_id,
                'mac_address': attributes.ATTR_NOT_SPECIFIED,
                'fixed_ips': attributes.ATTR_NOT_SPECIFIED,
                'n1kv:profile_id': self.n1kv_mgmt_pp_id(),
                'device_id': "",
                'device_owner': ""}}
            try:
                mgmt_port = self._core_plugin.create_port(context,
                                                          p_spec)
                # The trunk networks
                n_spec = {'network': {'tenant_id': tenant_id,
                                      'admin_state_up': True,
                                      'name': n1kv_const.T1_NETWORK_NAME,
                                      'shared': False}}
                # Until Nova allows spinning up VMs with VIFs on
                # networks without subnet(s) we create "dummy" subnets
                # for the trunk networks
                sub_spec = {'subnet': {
                    'tenant_id': tenant_id,
                    'admin_state_up': True,
                    'cidr': n1kv_const.SUB_PREFX,
                    'enable_dhcp': False,
                    'gateway_ip': attributes.ATTR_NOT_SPECIFIED,
                    'allocation_pools': attributes.ATTR_NOT_SPECIFIED,
                    'ip_version': 4,
                    'dns_nameservers': attributes.ATTR_NOT_SPECIFIED,
                    'host_routes': attributes.ATTR_NOT_SPECIFIED}}
                for i in xrange(0, max_hosted):
                    # Create T1 trunk network for this router
                    indx = str(i + 1)
                    n_spec['network'].update(
                        {'name': n1kv_const.T1_NETWORK_NAME + indx,
                         'n1kv:profile_id': self.n1kv_t1_np_id()})
                    t1_n.append(self._core_plugin.create_network(
                        context, n_spec))
                    LOG.debug(_('Created T1 network with name %(name)s and '
                                'id %(id)s'),
                              {'name': n1kv_const.T1_NETWORK_NAME + indx,
                               'id': t1_n[i]['id']})
                    # Create dummy subnet for this trunk network
                    sub_spec['subnet'].update(
                        {'name': n1kv_const.T1_SUBNET_NAME + indx,
                         'network_id': t1_n[i]['id']})
                    t1_sn.append(self._core_plugin.create_subnet(context,
                                                                  sub_spec))
                    # Create T1 port for this router
                    p_spec['port'].update(
                        {'name': n1kv_const.T1_PORT_NAME + indx,
                         'network_id': t1_n[i]['id'],
                         'n1kv:profile_id': self.n1kv_t1_pp_id()})
                    t_p.append(self._core_plugin.create_port(context, p_spec))
                    LOG.debug(_('Created T1 port with name %(name)s,  '
                                'id %(id)s and subnet %(subnet)s'),
                              {'name': t1_n[i]['name'],
                               'id': t1_n[i]['id'],
                               'subnet': t1_sn[i]['id']})
                    # Create T2 trunk network for this router
                    n_spec['network'].update(
                        {'name': n1kv_const.T2_NETWORK_NAME + indx,
                         'n1kv:profile_id': self.n1kv_t2_np_id()})
                    t2_n.append(self._core_plugin.create_network(context,
                                                                 n_spec))
                    LOG.debug(_('Created T2 network with name %(name)s and '
                                'id %(id)s'),
                              {'name': n1kv_const.T2_NETWORK_NAME + indx,
                               'id': t2_n[i]['id']})
                    # Create dummy subnet for this trunk network
                    sub_spec['subnet'].update(
                        {'name': n1kv_const.T2_SUBNET_NAME + indx,
                         'network_id': t2_n[i]['id']})
                    t2_sn.append(self._core_plugin.create_subnet(context,
                                                                 sub_spec))
                    # Create T2 port for this router
                    p_spec['port'].update(
                        {'name': n1kv_const.T2_PORT_NAME + indx,
                         'network_id': t2_n[i]['id'],
                         'n1kv:profile_id': self.n1kv_t2_pp_id()})
                    t_p.append(self._core_plugin.create_port(context, p_spec))
                    LOG.debug(_('Created T2 port with name %(name)s,  '
                                'id %(id)s and subnet %(subnet)s'),
                              {'name': t2_n[i]['name'],
                               'id': t2_n[i]['id'],
                               'subnet': t2_sn[i]['id']})
            except n_exc.NeutronException as e:
                LOG.error(_('Error %s when creating service VM resources. '
                            'Cleaning up.'), e)
                resources = {'ports': t_p, 'networks': t1_n + t2_n,
                             'subnets': t1_sn + t2_sn}
                self.delete_hosting_device_resources(
                    context, tenant_id, mgmt_port, **resources)
                mgmt_port = None
                t1_n, t1_sn, t2_n, t2_sn, t_p = [], [], [], [], []
        return {'mgmt_port': mgmt_port,
                'ports': t_p,
                'networks': t1_n + t2_n,
                'subnets': t1_sn + t2_sn}

    def get_hosting_device_resources(self, context, id, tenant_id, mgmt_nw_id):
        ports, nets, subnets = [], [], []
        mgmt_port = None
        for port in self._core_plugin.get_ports(context,
                                                filters={'device_id': [id]}):
            if port['network_id'] != mgmt_nw_id:
                ports.append(port)
                nets.append({'id': port['network_id']})
                subnets.append({'id': port['fixed_ips'][0]['subnet_id']})
            else:
                mgmt_port = port
        return {'mgmt_port': mgmt_port,
                'ports':ports, 'networks': nets, 'subnets': subnets}

    def delete_hosting_device_resources(self, context, tenant_id, mgmt_port,
                                        **kwargs):
         # Remove anything created.
        if mgmt_port is not None:
            try:
                self._core_plugin.delete_port(context, mgmt_port['id'])
            except n_exc.NeutronException as e:
                LOG.error(_('Failed to delete management port %(port_id)s for '
                            'service vm due to %(err)s'),
                          {'port_id': mgmt_port['id'], 'err': e})
        for item in kwargs['ports']:
            try:
                self._core_plugin.delete_port(context, item['id'])
            except n_exc.NeutronException as e:
                LOG.error(_('Failed to delete trunk port %(port_id)s for '
                            'service vm due to %(err)s'),
                          {'port_id': item['id'], 'err': e})
        for item in kwargs['subnets']:
            try:
                self._core_plugin.delete_subnet(context, item['id'])
            except n_exc.NeutronException as e:
                LOG.error(_('Failed to delete subnet %(subnet_id)s for '
                            'service vm due to %(err)s'),
                          {'subnet_id': item['id'], 'err': e})
        for item in kwargs['networks']:
            try:
                self._core_plugin.delete_network(context, item['id'])
            except n_exc.NeutronException as e:
                LOG.error(_('Failed to delete trunk network %(net_id)s for '
                            'service vm due to %(err)s'),
                          {'net_id': item['id'], 'err': e})

    def setup_logical_port_connectivity(self, context, port_db):
        # Add the VLAN to the VLANs that the hosting port trunks.
        self._perform_logical_port_connectivity_action(
            context, port_db, 'Adding', n1kv_profile.SEGMENT_ADD)

    def teardown_logical_port_connectivity(self, context, port_db):
        # Remove the VLAN from the VLANs that the hosting port trunks.
        self._perform_logical_port_connectivity_action(
            context, port_db, 'Removing', n1kv_profile.SEGMENT_DEL)

    def extend_hosting_port_info(self, context, port_db, hosting_info):
        hosting_info['segmentation_id'] = port_db.hosting_info.segmentation_tag

    def allocate_hosting_port(self, context, router_id, port_db, network_type,
                              hosting_device_id):
        allocations = self._get_router_ports_with_hosting_info_qry(
            context, router_id).all()
        trunk_mappings = {}
        if len(allocations) == 0:
            # Router has no ports with hosting port allocated to them yet
            # whatsoever, so we select an unused port (that trunks networks
            # of correct type) on the hosting device.
            id_allocated_port = self._get_unused_service_vm_trunk_port(
                 context, hosting_device_id, network_type)
        else:
            # Router has at least one port with hosting port allocated to it.
            # If there is only one allocated hosting port then it may be for
            # the wrong network type. Iterate to determine the hosting port.
            id_allocated_port = None
            for item in allocations:
                if item.hosting_info['network_type'] == network_type:
                    # For VXLAN we need to determine used link local tags.
                    # For VLAN we don't need to but the following lines will
                    # be performed once anyway since we break out of the
                    # loop later. That does not matter.
                    tag = item.hosting_info['segmentation_tag']
                    trunk_mappings[item['network_id']] = tag
                    id_allocated_port = item.hosting_info['hosting_port_id']
                else:
                    port_twin_id = item.hosting_info['hosting_port_id']
                if network_type == 'vlan':
                    # For a router port belonging to a VLAN network we can
                    # break here since we now know (or have information to
                    # determine) hosting_port and the VLAN tag is provided by
                    # the core plugin.
                    break
            if id_allocated_port is None:
                # Router only had hosting port for wrong network
                # type allocated yet. So get that port's sibling.
                id_allocated_port = self._get_other_port_id_in_pair(
                    context, port_twin_id, hosting_device_id)
        if id_allocated_port is None:
            # Database must have been messed up if this happens ...
            return
        if network_type == 'vxlan':
            # For VLXAN we choose the (link local) VLAN tag
            used_tags = set(trunk_mappings.values())
            allocated_vlan = min(sorted(FULL_VLAN_SET - used_tags))
        else:
            # For VLAN core plugin provides VLAN tag.
            trunk_mappings[port_db['network_id']] = None
            tags = self._core_plugin.get_networks(
                context,  {'id': [port_db['network_id']]},
                [pr_net.SEGMENTATION_ID])
            allocated_vlan = (None if tags == []
                              else tags[0].get(pr_net.SEGMENTATION_ID))
        if allocated_vlan is None:
            # Database must have been messed up if this happens ...
            return
        return {'allocated_port_id': id_allocated_port,
                'allocated_vlan': allocated_vlan}

    def _perform_logical_port_connectivity_action(self, context, port_db,
                                                  action_str, action):
        if (port_db is None or port_db.hosting_info is None or
                port_db.hosting_info.hosting_port is None):
            return
        np_id_t_nw = self._core_plugin.get_network(
            context, port_db.hosting_info.hosting_port['network_id'],
            [n1kv_profile.PROFILE_ID])
        if np_id_t_nw.get(n1kv_profile.PROFILE_ID) == self.n1kv_t1_np_id():
            # for vxlan trunked segment, id:s end with ':'link local vlan tag
            trunk_spec = (port_db['network_id'] + ':' +
                          str(port_db.hosting_info.segmentation_tag))
        else:
            trunk_spec = port_db['network_id']
        LOG.info(_('Updating trunk: %(action) VLAN %(tag) for network_id '
                   '%(id)'), {'action': action,
                              'tag': port_db.hosting_info.segmentation_tag,
                              'id': port_db['network_id']})
        #TODO(bobmel): enable line below when N1kv does not trunk all
        self._core_plugin.update_network(
            context, port_db.hosting_info.hosting_port['network_id'],
            {'network': {action: trunk_spec}})

    def _get_trunk_mappings(self, context, hosting_port_id):
        query = context.session.query(HostedHostingPortBinding)
        query = query.filter(
            HostedHostingPortBinding.hosting_port_id == hosting_port_id)
        return {hhpb.router_port['network_id']: hhpb.segmentation_tag
                for hhpb in query}

    def _get_unused_service_vm_trunk_port(self, context, hd_id, network_type):
        name = (n1kv_const.T2_PORT_NAME if network_type == 'vlan'
                else n1kv_const.T1_PORT_NAME)
        attempts = 0
        while True:
            # mysql> SELECT * FROM ports WHERE device_id = 'hd_id1' AND
            # id NOT IN (SELECT hosting_port_id FROM hostedhostingportbindings) AND
            # name LIKE '%t1%'
            # ORDER BY name;
            stmt = context.session.query(
                HostedHostingPortBinding.hosting_port_id).subquery()
            query = context.session.query(models_v2.Port.id)
            query = query.filter(and_(models_v2.Port.device_id == hd_id,
                                      ~models_v2.Port.id.in_(stmt),
                                      models_v2.Port.name.like('%' + name +
                                                               '%')))
            query = query.order_by(models_v2.Port.name)
            res = query.first()
            if res is None:
                if attempts >= MAX_HOSTING_PORT_LOOKUP_ATTEMPTS:
                    # This should not happen ...
                    LOG.error(_('Hosting port DB inconsistency for '
                                'hosting device %s'), hd_id)
                    return
                else:
                    # The service VM may not have plugged its VIF into the
                    # Neutron Port yet so we wait and make another lookup.
                    attempts += 1
                    LOG.info(_('Attempt %(attempt)d to find trunk ports for '
                               'hosting device %(hd_id)s failed. Trying '
                               'again in %(time)d seconds.'),
                             {'attempt': attempts, 'hd_id': hd_id,
                              'time': SECONDS_BETWEEN_HOSTING_PORT_LOOKSUPS})
                    eventlet.sleep(SECONDS_BETWEEN_HOSTING_PORT_LOOKSUPS)
            else:
                break
        return res[0]

    def _get_router_ports_with_hosting_info_qry(self, context, router_id,
                                                device_owner=None,
                                                hosting_port_id=None):
        # Query for a router's ports that have trunking information
        query = context.session.query(models_v2.Port)
        query = query.join(
            HostedHostingPortBinding,
            models_v2.Port.id == HostedHostingPortBinding.router_port_id)
        query = query.filter(models_v2.Port.device_id == router_id)
        if device_owner is not None:
            query = query.filter(models_v2.Port.device_owner == device_owner)
        if hosting_port_id is not None:
            query = query.filter(
                HostedHostingPortBinding.hosting_port_id == hosting_port_id)
        return query

    def _get_other_port_id_in_pair(self, context, port_id, hosting_device_id):
        query = context.session.query(models_v2.Port)
        query = query.filter(models_v2.Port.id == port_id)
        try:
            port = query.one()
            name, index = port['name'].split(':')
            name += ':'
            if name == n1kv_const.T1_PORT_NAME:
                other_port_name = n1kv_const.T2_PORT_NAME + index
            else:
                other_port_name = n1kv_const.T1_PORT_NAME + index
            query = context.session.query(models_v2.Port)
            query = query.filter(models_v2.Port.device_id == hosting_device_id,
                                 models_v2.Port.name == other_port_name)
            other_port = query.one()
            return other_port['id']
        except (exc.NoResultFound, exc.MultipleResultsFound):
            # This should not happen ...
            LOG.error(_('Port trunk pair DB inconsistency for port %s'),
                      port_id)
            return