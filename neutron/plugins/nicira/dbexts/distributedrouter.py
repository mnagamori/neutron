# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright 2013 Nicira Networks, Inc.  All rights reserved.
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
# @author: Salvatore Orlando, Nicira, Inc
#

from neutron.plugins.nicira.dbexts import nsxrouter
from neutron.plugins.nicira.extensions import distributedrouter as dist_rtr


class DistributedRouter_mixin(nsxrouter.NsxRouterMixin):
    """Mixin class to enable distributed router support."""

    nsx_attributes = (
        nsxrouter.NsxRouterMixin.nsx_attributes + [{
            'name': dist_rtr.DISTRIBUTED,
            'default': False
        }])
