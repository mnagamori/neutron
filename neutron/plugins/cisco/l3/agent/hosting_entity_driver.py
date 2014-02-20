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

from abc import ABCMeta, abstractmethod


class RoutingDriver():
    __metaclass__ = ABCMeta

    @abstractmethod
    def router_added(self, router_info):
        pass

    @abstractmethod
    def router_removed(self, router_info, deconfigure=True):
        pass

    @abstractmethod
    def internal_network_added(self, router_info, port):
        pass

    @abstractmethod
    def internal_network_removed(self, router_info, port):
        pass

    @abstractmethod
    def external_gateway_added(self, router_info, ex_gw_port, internal_ports):
        pass

    @abstractmethod
    def external_gateway_removed(self, router_info, ex_gw_port, internal_ports):
        pass

    @abstractmethod
    def handle_snat(self, router_info, ex_gw_port, internal_ports, action):
        pass

    @abstractmethod
    def floating_ip_added(self, router_info, ex_gw_port, floating_ip, fixed_ip):
        pass

    @abstractmethod
    def floating_ip_removed(self, router_info, ex_gw_port, floating_ip, fixed_ip):
        pass

    @abstractmethod
    def routes_updated(self, router_info, action, route):
        pass


