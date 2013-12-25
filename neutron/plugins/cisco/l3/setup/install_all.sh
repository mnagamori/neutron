#!/bin/bash

# Runs all install and demo scripts in the right order.

# osn is the name of Openstack network service, i.e.,
# it should be either 'neutron' or 'quantum', for
# release >=Havana and release <=Grizzly, respectively.
osn=${1:-neutron}
plugin=${2:-n1kv}
#plugin=${2:-osn}

source ~/devstack/openrc admin demo
./setup_keystone_for_csr1kv_l3.sh $osn
source ~/devstack/openrc $osn L3AdminTenant
./setup_nova_and_glance_for_csr1kv_l3.sh $osn
./setup_neutron_for_csr1kv_l3.sh $osn $plugin
sudo -E ./setup_l3cfgagent_networking.sh $osn $plugin
source ~/devstack/openrc admin demo
./setup_test_networks.sh $osn $plugin
./setup_interface_on_extnet1_for_demo.sh $osn $plugin

