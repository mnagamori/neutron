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

import sqlalchemy as sa
from sqlalchemy import orm

from neutron.db import agents_db
from neutron.db import l3_db
from neutron.db import model_base
from neutron.db import models_v2


class HostingDeviceTemplate(model_base.BASEV2, models_v2.HasId,
                            models_v2.HasTenant):
    """Represents a template for hosting devices."""
    # user-friendly name given to hosting devices created
    # using this template
    name = sa.Column(sa.String(255))
    # template enabled if True
    enabled = sa.Column(sa.Boolean, nullable=False, default=True)
    # 'host_category' can be 'VM', 'Hardware'
    host_category = sa.Column(sa.String(255), nullable=False)
    # 'host_type' can be 'NetworkNamespaceNode', 'CSR1kv', ...
    host_type = sa.Column(sa.String(255), nullable=False)
    # list of services hosting devices created using this
    # template support
    service_types = sa.Column(sa.String(255))
    # the image name or uuid in Glance
    image = sa.Column(sa.String(255))
    # the VM flavor or uuid in Nova
    flavor = sa.Column(sa.String(255))
    # 'configuration_mechanism' indicates how configurations are made
    configuration_mechanism = sa.Column(sa.String(255))
    # 'transport_port' is udp/tcp port of hosting device. May be empty.
    transport_port = sa.Column(sa.Integer)
    # Typical time (in seconds) needed for hosting device (created
    # from this template) to boot into operational state.
    booting_time = sa.Column(sa.Integer, default=0)
    #TODO(bobmel): Drop 'capacities', and replace by 'capacity' that is a
    #TODO          measure of slots. Then introduce '???' which is a list
    #TODO          specifying how many slots the logical resources need.
    # list of num_<resource>:<num_instances>, ...
    capacities = sa.Column(sa.String(255))
    # 'tenant_bound' is empty or is id of the only tenant allowed to
    # own/place resources on hosting devices created using this template
    tenant_bound = sa.Column(sa.String(255))
    # module to be used as plugging driver for logical resources
    # hosted inside hosting devices created using this template
    device_driver = sa.Column(sa.String(255), nullable=False)
    # module to be used as hosting device driver when creating
    # hosting devices using his template
    plugging_driver = sa.Column(sa.String(255), nullable=False)
    # modules to be used by configuration agent when configuring
    # logical resources in hosting_devices created using this template.
    # router:<module>, fw:<module>, vpn:<module>, ... or all:<module>
    cfg_agent_drivers = sa.Column(sa.String(512), nullable=False)
    # modules to be used as scheduler for logical resources hosted
    # inside hosting devices created using this template,
    # router:<module>,fw:<module>,vpn:<module>, ... or all:<module>
    schedulers = sa.Column(sa.String(512), nullable=False)


#TODO(bobmel): Need to store credentials somewhere/somehow.
class HostingDevice(model_base.BASEV2, models_v2.HasId, models_v2.HasTenant):
    """Represents an appliance hosting Neutron router(s). When the
       hosting device is a Nova VM 'id' is uuid of that VM."""
    # id of hosting device template used to create the hosting device
    template_id = sa.Column(sa.String(36),
                            sa.ForeignKey('hostingdevicetemplates.id'))
    admin_state_up = sa.Column(sa.Boolean, nullable=False, default=True)
    # 'host_category' can be 'VM', 'Hardware'
    host_category = sa.Column(sa.String(255), nullable=False)
    # 'host_type' can be 'NetworkNamespaceNode', 'CSR1kv', ...
    host_type = sa.Column(sa.String(255), nullable=False)
    # 'ip_address' is address of hosting device's management interface
    ip_address = sa.Column(sa.String(64), nullable=False)
    # 'transport_port' is udp/tcp port of hosting device. May be empty.
    transport_port = sa.Column(sa.Integer)
    cfg_agent_id = sa.Column(sa.String(36),
                             sa.ForeignKey('agents.id'),
                             nullable=True)
    cfg_agent = orm.relationship(agents_db.Agent)
    # Service VMs take time to boot so we store creation time
    # so we can give preference to older ones when scheduling
    created_at = sa.Column(sa.DateTime, nullable=False)
    # Typical time (in seconds) needed for hosting device to boot
    # into operational state.
    booting_time = sa.Column(sa.Integer, default=0)
    status = sa.Column(sa.String(16))
    # 'tenant_bound' is empty or is id of the only tenant allowed to
    # own/place resources on this hosting device
    tenant_bound = sa.Column(sa.String(255))
    auto_delete_on_fail = sa.Column(sa.Boolean, default=True, nullable=False)


class RouterTypeHostingDeviceTemplateBinding(model_base.BASEV2):
    """Represents binding between a Neutron router type and
       hosting device template used to created them."""
    router_type = sa.Column(sa.String(255), nullable=False)
    template_id = sa.Column(sa.String(36),
                            sa.ForeignKey('hostingdevicetemplates.id',
                                          ondelete='CASCADE'),
                            primary_key=True)


class RouterHostingDeviceBinding(model_base.BASEV2):
    """Represents binding between Neutron routers and their hosting devices"""
    router_id = sa.Column(sa.String(36),
                          sa.ForeignKey('routers.id', ondelete='CASCADE'),
                          primary_key=True)
    router = orm.relationship(l3_db.Router)
    # 'router_type' can be 'NetworkNamespace', 'CSR1kv', ...
    router_type = sa.Column(sa.String(255), nullable=False)
    # If 'auto_schedule' is True then router is automatically scheduled
    # if it lacks a hosting device or its hosting device fails.
    auto_schedule = sa.Column(sa.Boolean, default=True, nullable=False)
    share_hosting_device = sa.Column(sa.Boolean, default=True, nullable=False)
    hosting_device_id = sa.Column(sa.String(36),
                                  sa.ForeignKey('hostingdevices.id',
                                                ondelete='SET NULL'))
    hosting_device = orm.relationship(HostingDevice)


class HostedHostingPortBinding(model_base.BASEV2):
    """Represents binding of a router port to its hosting port."""
    router_id = sa.Column(sa.String(36),
                          sa.ForeignKey('routers.id',
                                        ondelete="CASCADE"),
                          primary_key=True)
    router_port_id = sa.Column(sa.String(36),
                               sa.ForeignKey('ports.id',
                                             ondelete="CASCADE"),
                               primary_key=True)
    router_port = orm.relationship(models_v2.Port,
                                   primaryjoin='Port.id==HostedHostingPortBinding.'
                                               'router_port_id',
                                   backref=orm.backref('hosting_info',
                                                       cascade='all',
                                                       uselist=False))
    # type of router port: router_interface, ..._gateway, ..._floatingip
    port_type = sa.Column(sa.String(32))
    # type of network the router port belongs to
    network_type = sa.Column(sa.String(32))
    hosting_port_id = sa.Column(sa.String(36),
                                sa.ForeignKey('ports.id',
                                              ondelete='SET NULL'))
    hosting_port = orm.relationship(models_v2.Port,
                                    primaryjoin='Port.id==HostedHostingPortBinding.'
                                                'hosting_port_id')
    segmentation_tag = sa.Column(sa.Integer,
                                 autoincrement=False)
