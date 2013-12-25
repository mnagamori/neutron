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

import netaddr
import os

from oslo.config import cfg

from neutron import manager
from neutron.plugins.cisco.l3.hosting_device_drivers import HostingDeviceDriver
from neutron.openstack.common import log as logging

LOG = logging.getLogger(__name__)


# Length mgmt port UUID to be part of VM's config drive filename
CFG_DRIVE_UUID_START = 24
CFG_DRIVE_UUID_LEN = 12

CSR1KV_HD_DRIVER_OPTS = [
    cfg.StrOpt('csr_config_template', default='csr_cfg_template',
               help=_("CSR default template file name")),
]

cfg.CONF.register_opts(CSR1KV_HD_DRIVER_OPTS)

class CSR1kvHostingDeviceDriver(HostingDeviceDriver):

    @property
    def _core_plugin(self):
        return manager.NeutronManager.get_plugin()

    def create_configdrive_files(self, context, mgmtport):
        mgmt_ip = mgmtport['fixed_ips'][0]['ip_address']
        subnet_data = self._core_plugin.get_subnet(
            context, mgmtport['fixed_ips'][0]['subnet_id'],
            ['cidr', 'gateway_ip', 'dns_nameservers'])
        netmask = str(netaddr.IPNetwork(subnet_data['cidr']).netmask)
        params = {'<ip>': mgmt_ip, '<mask>': netmask,
                  '<gw>': subnet_data['gateway_ip'],
                  '<name_server>': '8.8.8.8'}
        try:
            cfg_template_filename = (cfg.CONF.templates_path + "/" +
                                     cfg.CONF.csr_config_template)
            vm_cfg_filename = self._unique_cfgdrive_filename(mgmtport['id'])
            cfg_template_file = open(cfg_template_filename, 'r')
            vm_cfg_file = open(vm_cfg_filename, "w")
            # insert proper instance values in the template
            for line in cfg_template_file:
                tokens = line.strip('\n').split(' ')
                result = [params[token] if token in params.keys()
                          else token for token in tokens]
                line = ' '.join(map(str, result)) + '\n'
                vm_cfg_file.write(line)
            vm_cfg_file.close()
            cfg_template_file.close()
            return {'iosxe_config.txt': vm_cfg_filename}
        except IOError as e:
            LOG.error(_('Failed to create config file: %s. Trying to'
                        'clean up.'), str(e))
            self.delete_configdrive_files(context, mgmtport)
            raise

    def delete_configdrive_files(self, context, mgmtport):
        try:
            os.remove(self._unique_cfgdrive_filename(mgmtport['id']))
        except OSError as e:
            LOG.error(_('Failed to delete config file: %s'), str(e))

    def _unique_cfgdrive_filename(self, uuid):
        end = CFG_DRIVE_UUID_START + CFG_DRIVE_UUID_LEN
        return (cfg.CONF.service_vm_config_path + "/csr1kv_" +
                uuid[CFG_DRIVE_UUID_START:end] + ".cfg")
