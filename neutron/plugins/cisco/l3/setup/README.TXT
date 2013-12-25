The easy way to do everything in one go:
./install <neutron/quantum> <n1kv/ovs>

# The first parameter is the name of Openstack network service,
# i.e., it should be either 'neutron' or 'quantum', for
# release >=Havana and release <=Grizzly, respectively.

# The second parameter should be n1kv when Cisco n1kv plugin is used.
# *If* you have the openvswitch plugin patched for trunking support
# then you can set it to ovs.

That's it!


You can run the scripts manually in the following order:
Note that the keystone script must always be run once before
the other scripts can be used.

$ source [path to devstack directory]/openrc admin demo
$ ./setup_keystone_for_csr1kv_l3.sh <neutron/quantum>
$ source [path to devstack directory]/openrc quantum L3AdminTenant
$ ./setup_nova_and_glance_for_csrk1v.sh <neutron/quantum>
$ ./setup_neutron_for_csr1kv.sh <neutron/quantum> <n1kv/ovs>
$ sudo -E ./setup_l3cfgagent_networking.sh <neutron/quantum> <n1kv/ovs>
$ source [path to devstack directory]/openrc admin demo
$ ./setup_test_networks.sh <neutron/quantum> <n1kv/ovs>
$ ./setup_interface_on_extnet1_for_demo.sh <neutron/quantum> <n1kv/ovs>
