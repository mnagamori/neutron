# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright (c) 2014 OpenStack Foundation.
# All rights reserved.
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

from abc import abstractmethod

import webob.exc

from neutron.api import extensions
from neutron.api.v2 import attributes
from neutron.api.v2 import base
from neutron.api.v2 import resource
from neutron.common import exceptions
from neutron import manager
from neutron.openstack.common import log as logging
from neutron.plugins.common import constants as service_constants
from neutron import wsgi

LOG = logging.getLogger(__name__)


NAME = 'cisco_router'
TYPE = NAME + ':type'
ROUTER_TYPES = TYPE + 's'

EXTENDED_ATTRIBUTES_2_0 = {
    'routers': {
        TYPE: {'allow_post': True, 'allow_put': False,
               'validate': {'type:string': None},
               'default': attributes.ATTR_NOT_SPECIFIED,
               'is_visible': True},
    }
}


class RouterTypeController(wsgi.Controller):
    def get_plugin(self):
        plugin = manager.NeutronManager.get_service_plugins().get(
            service_constants.L3_ROUTER_NAT)
        if not plugin:
            LOG.error(_('No plugin for L3 routing registered to handle '
                        'router scheduling'))
            msg = _('The resource could not be found.')
            raise webob.exc.HTTPNotFound(msg)
        return plugin

    def index(self, request, **kwargs):
        plugin = self.get_plugin()
        return plugin.list_router_types(request.context, kwargs['router_id'])


class Router_type(extensions.ExtensionDescriptor):
    """Extension class to define different types of Neutron routers.

    This class is used by Neutron's extension framework to support
    definition of different types of Neutron Routers.

    Attribute 'router_type' is a string name representing a certain
    router type. It can be set during creation of Neutron router.
    If a Neutron router is moved to a hosting device of a different
    hosting device type, the router type of the Neutron router will
    also change.

    To create a router of router type <name>:

       (shell) router-create <router_name> --cisco_router:type <name>
    """

    @classmethod
    def get_name(cls):
        return "Router types for routing service"

    @classmethod
    def get_alias(cls):
        return NAME

    @classmethod
    def get_description(cls):
        return "Introduces router_type attribute for Neutron Routers"

    @classmethod
    def get_namespace(cls):
        return "http://docs.openstack.org/ext/" + NAME + "/api/v1.0"

    @classmethod
    def get_updated(cls):
        return "2014-02-07T10:00:00-00:00"

    @classmethod
    def get_resources(cls):
        """Returns Ext Resources."""
        exts = []
        parent = dict(member_name="router",
                      collection_name="routers")
        controller = resource.Resource(RouterTypeController(),
                                       base.FAULT_MAP)
        exts.append(extensions.ResourceExtension(
            ROUTER_TYPES, controller, parent))
        return exts

    def get_extended_resources(self, version):
        if version == "2.0":
            return EXTENDED_ATTRIBUTES_2_0
        else:
            return {}


# router_type exceptions
class UndefinedRouterType(exceptions.NeutronException):
    message = _("Router type %(type) does not exist")


class RouterTypeAlreadyDefined(exceptions.NeutronException):
    message = _("Router type %(type) already exists")


class NoSuchHostingDeviceTemplateForRouterType(exceptions.NeutronException):
    message = _("No hosting device template with id %(type) exists")


class HostingDeviceTemplateUsedByRouterType(exceptions.NeutronException):
    message = _("Router type %(type) already defined for Hosting device "
                "template with id %(type)")


class RouterTypeHasRouters(exceptions.NeutronException):
    message = _("Router type %(type) cannot be deleted since routers "
                "of that type exists")


class RouterTypePluginBase(object):
    """REST API to manage router types.

    All methods except listing require admin context.
    """

    @abstractmethod
    def list_router_types(self, context):
        """Lists defined router types. For request in admin context
           hosting_device_template_id is included in response.
        """
        pass

    @abstractmethod
    def create_router_type(self, context, type_name,
                           hosting_device_template_id):
        """Creates a router type and binds it to the specified
           hosting device template."""
        pass

    @abstractmethod
    def delete_router_type(self, context, type_name):
        """Deletes a router type."""
        pass