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
# @author: Bob Melander, Cisco Systems, Inc.

from oslo.config import cfg
from novaclient.v1_1 import client
from novaclient import exceptions as nova_exc
from novaclient import utils as n_utils

from neutron.common import exceptions as n_exc
from neutron import manager
from neutron.openstack.common import uuidutils
from neutron.openstack.common import log as logging
from neutron.plugins.cisco.l3.common import constants

LOG = logging.getLogger(__name__)


SERVICE_VM_LIB_OPTS = [
    cfg.StrOpt('templates_path',
               default='/opt/stack/data/neutron/cisco/templates',
               help=_("Path to default templates")),
    cfg.StrOpt('service_vm_config_path',
               default='/opt/stack/data/neutron/cisco/config_drive',
               help=_("Path to config drive files for service VMs")),
]

cfg.CONF.register_opts(SERVICE_VM_LIB_OPTS)


class ServiceVMManager:

    def __init__(self, user=None, passwd=None, l3_admin_tenant=None,
                 auth_url=None):
        self._nclient = client.Client(user, passwd, l3_admin_tenant, auth_url,
                                      service_type="compute")

    @property
    def _core_plugin(self):
        return manager.NeutronManager.get_plugin()

    def get_service_vm_status(self, vm_id):
        try:
            status = self._nclient.servers.get(vm_id).status
        except (nova_exc.UnsupportedVersion, nova_exc.CommandError,
                nova_exc.AuthorizationFailure, nova_exc.NoUniqueMatch,
                nova_exc.AuthSystemNotFound, nova_exc.NoTokenLookupException,
                nova_exc.EndpointNotFound, nova_exc.AmbiguousEndpoints,
                nova_exc.ConnectionRefused, nova_exc.ClientException) as e:
            LOG.error(_('Failed to get status of service VM instance %(id)s, '
                        'due to %(err)s'), {'id': vm_id, 'err': e})
            status = constants.SVM_ERROR
        return status

#    def dispatch_service_vm_dis(self, context, instance_name, vm_image,
#                                vm_flavor, hosting_device_drv, mgmt_port,
#                                ports=None):
    def dispatch_service_vm(self, context, instance_name, vm_image, vm_flavor,
                            hosting_device_drv, mgmt_port, ports=None):
        nics = [{'port-id': mgmt_port['id']}]
        for port in ports:
            nics.append({'port-id': port['id']})

        try:
            image = n_utils.find_resource(self._nclient.images, vm_image)
            flavor = n_utils.find_resource(self._nclient.flavors, vm_flavor)
        except nova_exc.CommandError as e:
            LOG.error(_('Failure: %s'), e)
            return None

        try:
            # Assumption for now is that this does not need to be
            # plugin dependent, only hosting device type dependent.
            cfg_files = hosting_device_drv.create_configdrive_files(
                context, mgmt_port)
            files = {label: open(name) for label, name in cfg_files.items()}
        except IOError:
            return None

        try:
            server = self._nclient.servers.create(
                instance_name, image.id, flavor.id, nics=nics, files=files,
                config_drive=(files != {}))
        except (nova_exc.UnsupportedVersion, nova_exc.CommandError,
                nova_exc.AuthorizationFailure, nova_exc.NoUniqueMatch,
                nova_exc.AuthSystemNotFound, nova_exc.NoTokenLookupException,
                nova_exc.EndpointNotFound, nova_exc.AmbiguousEndpoints,
                nova_exc.ConnectionRefused, nova_exc.ClientException) as e:
            LOG.error(_('Failed to create service VM instance: %s'), e)
            hosting_device_drv.delete_configdrive_files(context, mgmt_port)
            return None
        res = {'id': server.id}
        return res

#    def delete_service_vm_dis(self, context, vm_id, hosting_device_drv,
#                              mgmt_nw_id):
    def delete_service_vm(self, context, vm_id, hosting_device_drv,
                          mgmt_nw_id):
        result = True
        # Get ports on management network (should be only one)
        ports = self._core_plugin.get_ports(
            context, filters={'device_id': [id],
                              'network_id': [mgmt_nw_id]})
        if ports:
            hosting_device_drv.delete_configdrive_files(context, ports[0])
        try:
            self._nclient.servers.delete(vm_id)
        except (nova_exc.UnsupportedVersion, nova_exc.CommandError,
                nova_exc.AuthorizationFailure, nova_exc.NoUniqueMatch,
                nova_exc.AuthSystemNotFound, nova_exc.NoTokenLookupException,
                nova_exc.EndpointNotFound, nova_exc.AmbiguousEndpoints,
                nova_exc.ConnectionRefused, nova_exc.ClientException) as e:
            LOG.error(_('Failed to delete service VM instance %(id)s, '
                        'due to %(err)s'), {'id': vm_id, 'err': e})
            result = False
        return result

    # TODO(bobmel): Move this to fake_service_vm_lib.py file
    # with FakeServiceVMManager
#    def dispatch_service_vm(self, context, instance_name, vm_image, vm_flavor,
#                            hosting_device_drv, mgmt_port, ports=None):
    def dispatch_service_vm_fake(self, context, instance_name, vm_image,
                                 vm_flavor, hosting_device_drv, mgmt_port,
                                 ports=None):
        vm_id = uuidutils.generate_uuid()

        try:
            # Assumption for now is that this does not need to be
            # plugin dependent, only hosting device type dependent.
            cfg_files = hosting_device_drv.create_configdrive_files(
                context, mgmt_port)
            files = {label: open(name) for label, name in cfg_files.items()}
        except IOError:
            return None

        if mgmt_port is not None:
            p_dict = {'port': {'device_id': vm_id,
                               'device_owner': 'nova'}}
            self._core_plugin.update_port(context, mgmt_port['id'], p_dict)

        for port in ports:
            p_dict = {'port': {'device_id': vm_id,
                               'device_owner': 'nova'}}
            self._core_plugin.update_port(context, port['id'], p_dict)

        myserver = {'server': {'adminPass': "MVk5HPrazHcG",
                    'id': vm_id,
                    'links': [{'href': "http://openstack.example.com/v2/"
                                        "openstack/servers/" + vm_id,
                               'rel': "self"},
                                {'href': "http://openstack.example.com/"
                                          "openstack/servers/" + vm_id,
                                 'rel': "bookmark"}]}}

        return myserver['server']

#    def delete_service_vm(self, context, vm_id, hosting_device_drv,
#                          mgmt_nw_id):
    def delete_service_vm_fake(self, context, vm_id, hosting_device_drv,
                               mgmt_nw_id):
        result = True
        # Get ports on management network (should be only one)
        ports = self._core_plugin.get_ports(
            context, filters={'device_id': [vm_id],
                              'network_id': [mgmt_nw_id]})
        if ports:
            hosting_device_drv.delete_configdrive_files(context, ports[0])

        try:
            ports = self._core_plugin.get_ports(context,
                                                filters={'device_id': [vm_id]})
            for port in ports:
                self._core_plugin.delete_port(context, port['id'])
        except n_exc.NeutronException as e:
            LOG.error(_('Failed to delete service VM %(id)s due to %(err)s'),
                      {'id': vm_id, 'err': e})
            result = False
        return result
