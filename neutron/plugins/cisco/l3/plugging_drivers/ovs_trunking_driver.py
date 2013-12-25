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

from neutron.api.v2 import attributes
from neutron.common import exceptions as n_exc
from neutron import manager
from neutron.openstack.common import log as logging
from neutron.plugins.cisco.l3.common import n1kv_constants as n1kv_const
from neutron.plugins.cisco.l3.plugging_drivers import n1kv_trunking_driver

LOG = logging.getLogger(__name__)


TRUNKED_NETWORKS = 'trunkport:trunked_networks'


class OvsTrunkingPlugDriver(n1kv_trunking_driver.N1kvTrunkingPlugDriver):
    """This is a driver class for service VMs used together with
    the a patched version of the Openvswitch plugin that supports
    VLAN trunking.
    """
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
                'security_groups': [mgmt_sec_grp_id],
                'device_id': "",
                'device_owner': ""}}
            try:
                mgmt_port = self._core_plugin.create_port(context, p_spec)
                # No security groups on the trunk ports since
                # they have no IP address
                p_spec['port']['security_groups'] = []
                # The trunk networks
                n_spec = {'network': {'tenant_id': tenant_id,
                                      'admin_state_up': True,
                                      'name': n1kv_const.T1_NETWORK_NAME,
                                      'shared': False,
                                      TRUNKED_NETWORKS: {}}}
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
                        {'name': n1kv_const.T1_NETWORK_NAME + indx})
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
                         'network_id': t1_n[i]['id']})
                    t_p.append(self._core_plugin.create_port(context, p_spec))
                    LOG.debug(_('Created T1 port with name %(name)s,  '
                                'id %(id)s and subnet %(subnet)s'),
                              {'name': t1_n[i]['name'],
                               'id': t1_n[i]['id'],
                               'subnet': t1_sn[i]['id']})
                    # Create T2 trunk network for this router
                    n_spec['network'].update(
                        {'name': n1kv_const.T2_NETWORK_NAME + indx})
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
                         'network_id': t2_n[i]['id']})
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

    def setup_logical_port_connectivity(self, context, port_db):
        # Remove the VLAN from the VLANs that the hosting port trunks.
        if (port_db is None or port_db.hosting_info is None or
                port_db.hosting_info.hosting_port is None):
            return
        mappings = self._get_trunk_mappings(
            context, port_db.hosting_info.hosting_port['id'])
        mappings[port_db['network_id']] = port_db.hosting_info.segmentation_tag
        network_dict = {'network': {TRUNKED_NETWORKS: mappings}}
        self._core_plugin.update_network(
            context.elevated(),
            port_db.hosting_info.hosting_port['network_id'],
            network_dict)

    def teardown_logical_port_connectivity(self, context, port_db):
        # Remove the VLAN from the VLANs that the hosting port trunks.
        if (port_db is None or port_db.hosting_info is None or
                port_db.hosting_info.hosting_port is None):
            return
        mappings = self._get_trunk_mappings(
            context, port_db.hosting_info.hosting_port['id'])
        mappings.pop(port_db['network_id'])
        network_dict = {'network': {TRUNKED_NETWORKS: mappings}}
        self._core_plugin.update_network(
            context.elevated(),
            port_db.hosting_info.hosting_port['network_id'],
            network_dict)