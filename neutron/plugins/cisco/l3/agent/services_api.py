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
    """This class defines the API for the Routing and NAT related operations
       to be implemented by supported hosting devices
    """
    __metaclass__ = ABCMeta

    @abstractmethod
    def router_added(self, router_info):
        """ A logical router is assigned to the hosting device

           returns: None
           :param router_info: Router_info object for this router
        """
        pass

    @abstractmethod
    def router_removed(self, router_info):
        """A logical router is de-assigned from the hosting device

           returns: None
           :param router_info: Router_info object for this router
        """

        pass

    @abstractmethod
    def internal_network_added(self, router_info, port):
        """An internal network is connected to a router

            returns: None
            :param router_info: Router_info object for this router
            :param port : port dictionary for the port where the internal
                          network is connected
        """
        pass

    @abstractmethod
    def internal_network_removed(self, router_info, port):
        """An internal network is removed from a router

           returns: None
           :param router_info: Router_info object for this router
           :param port : port dictionary for the port where the internal
                         network was connected
        """
        pass

    @abstractmethod
    def external_gateway_added(self, router_info, ex_gw_port):
        """An external network is added to a router

           returns: None
           :param router_info: Router_info object of the router
           :param ex_gw_port : port dictionary for the port where the external
                               gateway network is connected
        """
        pass

    @abstractmethod
    def external_gateway_removed(self, router_info, ex_gw_port):
        """An external network is removed from the router

           returns: None
           :param router_info: Router_info object of the router
           :param ex_gw_port : port dictionary for the port where the external
                               gateway network was connected
        """
        pass

    @abstractmethod
    def enable_internal_network_NAT(self, router_info, port, ex_gw_port):
        """Enable NAT on an internal network

           returns: None
           :param router_info: Router_info object for this router
           :param port       : port dictionary for the port where the internal
                               network is connected
           :param ex_gw_port : port dictionary for the port where the external
                               gateway network is connected
        """
        pass

    @abstractmethod
    def disable_internal_network_NAT(self, router_info, port, ex_gw_port):
        """Diable NAT on an internal network

           returns: None
           :param router_info: Router_info object for this router
           :param port       : port dictionary for the port where the internal
                               network is connected
           :param ex_gw_port : port dictionary for the port where the external
                               gateway network is connected
        """
        pass

    @abstractmethod
    def floating_ip_added(self, router_info, ex_gw_port,
                          floating_ip, fixed_ip):
        """A floating IP is added

           returns: None
           :param router_info: Router_info object for this router
           :param ex_gw_port : port dictionary for the port where the external
                               gateway network is connected
           :param floating_ip: Floating IP as a string
           :param fixed_ip   : Fixed IP of internal internal interface as
                               a string
        """
        pass

    @abstractmethod
    def floating_ip_removed(self, router_info, ex_gw_port,
                            floating_ip, fixed_ip):
        """A floating IP is removed

           returns: None
           :param router_info: Router_info object for this router
           :param ex_gw_port : port dictionary for the port where the external
            gateway network is connected
           :param floating_ip: Floating IP as a string
           :param fixed_ip: Fixed IP of internal internal interface as a string 
        """
        pass

    @abstractmethod
    def routes_updated(self, router_info, action, route):
        """Routes are updated for router

           returns: None
           :param router_info: Router_info object for this router
           :param action : Action on the route , either 'replace' or 'delete'
           :param route: route dictionary with keys 'destination' & 'next_hop'
        """
        pass

