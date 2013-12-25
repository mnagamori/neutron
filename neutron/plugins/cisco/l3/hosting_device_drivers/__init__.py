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

from abc import ABCMeta, abstractmethod


class HostingDeviceDriver(object):
    """This class defines the API for hosting device drivers that are used
       by a Neutron (service) plugin to perform various (plugin independent)
       operations on hosting devices.
    """
    __metaclass__ = ABCMeta

    def hosting_device_name(self):
        pass

    def create_configdrive_files(self, context, mgmtport):
        """Creates configuration file(s) for a service VM that can be used
           to make initial configurations. The file(s) is/are injected in
           the VM's file system using Nova's configdrive feature.

           Called when a service VM-based hosting device is to be created.
           This function should cleanup after itself in case of error.

           returns: Dict with filenames and corresponding source filenames:
                    {filename1: src_filename1, filename2: src_filename2, ...}
                    The file system of the VM will contain files with the
                    specified filenames and content from the src_filename
                    files. If the dict is empty no configdrive will be used.

           :param context: neutron api request context.
           :param mgmt_port: management port for the hosting device.
        """
        pass

    def delete_configdrive_files(self, context, mgmtport):
        """Deletes any configuration file(s) used for the configdrive for a
           service VM.

           Called when a service VM-based hosting device is to be deleted.

           returns: -

           :param context: neutron api request context.
           :param mgmt_port: management port for the hosting device.
        """
        pass

