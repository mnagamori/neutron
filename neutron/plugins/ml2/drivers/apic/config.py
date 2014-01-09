# Copyright (c) 2013 OpenStack Foundation
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

from oslo.config import cfg


apic_opts = [
    cfg.StrOpt('apic_host',
               help=_("Host name or IP Address of the APIC controller")),
    cfg.StrOpt('apic_username',
               help=_("Username for the APIC controller")),
    cfg.StrOpt('apic_password',
               help=_("Password for the APIC controller")),
    cfg.StrOpt('apic_port',
               help=_("Communication port for the APIC controller")),
    cfg.StrOpt('apic_vmm_provider', default='VMware',
               help=_("Name for the VMM domain provider")),
    cfg.StrOpt('apic_vmm_domain', default='openstack',
               help=_("Name for the VMM domain to be created for Openstack")),
    cfg.StrOpt('apic_vlan_ns_name', default='openstack_ns',
               help=_("Name for the vlan namespace to be used for openstack")),
    cfg.StrOpt('apic_vlan_range', default='2:4093',
               help=_("Range of VLAN's to be used for Openstack")),
    cfg.StrOpt('apic_node_profile', default='openstack_profile',
               help=_("Name of the node profile to be created")),
]


cfg.CONF.register_opts(apic_opts, "ml2_apic")


class ML2MechApicConfig(object):
    switch_dict = {}

    def __init__(self):
        self._create_switch_dictionary()

    def _create_switch_dictionary(self):
        multi_parser = cfg.MultiConfigParser()
        read_ok = multi_parser.read(cfg.CONF.config_file)

        if len(read_ok) != len(cfg.CONF.config_file):
            raise cfg.Error(_("Some config files were not parsed properly"))

        for parsed_file in multi_parser.parsed:
            for parsed_item in parsed_file.keys():
                if parsed_item.startswith('switch'):
                    switch, switch_id = parsed_item.split(':')
                    if switch.lower() == 'switch':
                        self.switch_dict[switch_id] = {}
                        for host_list,port in parsed_file[parsed_item].items():
                            hosts = host_list.split(',')
                            port = port[0]
                            self.switch_dict[switch_id][port] = hosts