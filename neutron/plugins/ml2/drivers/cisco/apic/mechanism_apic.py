# Copyright (c) 2014 Cisco Systems Inc.
# All Rights Reserved.
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
# @author: Arvind Somya (asomya@cisco.com), Cisco Systems Inc.

import netaddr

from oslo.config import cfg

from neutron.extensions import portbindings
from neutron.openstack.common import log
from neutron.plugins.common import constants
from neutron.plugins.ml2 import driver_api as api
from neutron.plugins.ml2.drivers.cisco.apic.apic_manager import APICManager


LOG = log.getLogger(__name__)


class APICMechanismDriver(api.MechanismDriver):
    def initialize(self):
        self.vif_type = portbindings.VIF_TYPE_OVS
        self.cap_port_filter = False
        self.apic_manager = APICManager()

        # Create a VMM domain and VLAN namespace
        # Get vlan ns name
        ns_name = cfg.CONF.ml2_cisco_apic.apic_vlan_ns_name
        # Grab vlan ranges
        vlan_ranges = cfg.CONF.ml2_type_vlan.network_vlan_ranges[0]
        (vlan_min, vlan_max) = vlan_ranges.split(':')[-2:]
        # Create VLAN namespace
        vlan_ns = self.apic_manager.ensure_vlan_ns_created_on_apic(ns_name,
                                                                   vlan_min,
                                                                   vlan_max)
        vmm_name = cfg.CONF.ml2_cisco_apic.apic_vmm_domain
        # Create VMM domain
        self.apic_manager.ensure_vmm_domain_created_on_apic(vmm_name, vlan_ns)

        # Create entity profile
        ent_name = cfg.CONF.ml2_cisco_apic.apic_entity_profile
        self.apic_manager.ensure_entity_profile_created_on_apic(ent_name)

        # Create function profile
        func_name = cfg.CONF.ml2_cisco_apic.apic_function_profile
        self.apic_manager.ensure_function_profile_created_on_apic(func_name)

        # Create infrastructure on apic
        self.apic_manager.ensure_infra_created_on_apic()

    def create_port_postcommit(self, context):
        # Get tenant details from port context
        tenant_id = context.current['tenant_id']

        # Get network
        network = context.network.current['id']
        net_name = context.network.current['name']
        # Get segmentation id
        seg = context.bound_segment.get(api.SEGMENTATION_ID)

        # Get host binding if any
        host = context.current.get(portbindings.HOST_ID)

        # Check if port is bound to a host
        if not host:
            # Not a VM port, return for now
            return

        # Create a static path attachment for this host/epg/switchport combo
        self.apic_manager.ensure_tenant_created_on_apic(tenant_id)
        self.apic_manager.ensure_path_created_for_port(tenant_id, network,
                                                       host, seg, net_name)

    def create_network_postcommit(self, context):
        net_id = context.current['id']
        tenant_id = context.current['tenant_id']
        net_name = context.current['name']

        self.apic_manager.ensure_bd_created_on_apic(tenant_id, net_id)
        # Create EPG for this network
        self.apic_manager.ensure_epg_created_for_network(tenant_id, net_id,
                                                         net_name)

    def delete_network_postcommit(self, context):
        net_id = context.current['id']
        tenant_id = context.current['tenant_id']

        self.apic_manager.delete_bd_on_apic(tenant_id, net_id)
        self.apic_manager.delete_epg_for_network(tenant_id, net_id)

    def create_subnet_postcommit(self, context):
        tenant_id = context.current['tenant_id']
        network_id = context.current['network_id']
        gateway_ip = context.current['gateway_ip']
        cidr = netaddr.IPNetwork(context.current['cidr'])
        netmask = str(cidr.prefixlen)
        gateway_ip = gateway_ip + '/' + netmask

        self.apic_manager.ensure_subnet_created_on_apic(tenant_id, network_id,
                                                        gateway_ip)

    @staticmethod
    def check_segment(segment):
        """Verify a segment is valid for the APIC Mechanism driver."""
        network_type = segment[api.NETWORK_TYPE]

        return network_type in [constants.TYPE_VLAN]

    def validate_port_binding(self, context):
        if self.check_segment(context.bound_segment):
            LOG.debug(_('Binding valid.'))
            return True
        LOG.warning(_("Binding invalid for port: %s"), context.current)
