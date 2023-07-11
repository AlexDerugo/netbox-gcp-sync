
from    googleapiclient import discovery
from    google.oauth2 import service_account
import  pynetbox

# netbox connection
NETBOX_URL          = "http://netbox.url"
TOKEN               = "token"
nb                  = pynetbox.api(url=NETBOX_URL, token=TOKEN)

# GCP connection
svc_account_key     = "svc_account.json"
credentials         = service_account.Credentials.from_service_account_file(svc_account_key)

# global variable to which IP VMs from GCP will be added - public and internal, which are NOT from default vpc
ip_from_gcp_list    = {"ip"  : [],}
# a global variable to which projects with a list of VMs with parameters to be added to netbox will be added
# format: instance_list_per_project = {project1: [{VM1},{VM2}], project2: [{VM1},{VM2}]}
instance_list_per_project = {}

# netbox name of the cluster group and the type of clusters. each project will be equal to a cluster in netbox
cluster_group_name  = "gcp-organization"
cluster_type_name   = "gcp-project"

# creating a tag in netbox, this tag will be on all objects created with this script
tag_gcp_name        = "netbox-gcp-sync"
tag_gcp             = nb.extras.tags.get(name = tag_gcp_name)
# if the tag already exists, then we get its id, which we will use to add to other objects
if tag_gcp :
    tag_gcp_id      = tag_gcp.id
# if there is no tag, then we create it and get its id, which we will use to add to other objects
else:
    tag_gcp         = nb.extras.tags.create({"name": tag_gcp_name, "slug": tag_gcp_name})
    tag_gcp_id      = tag_gcp.id


# a function that returns a list of projects that have the compute API enabled, so as not to make invalid requests to find VMs in projects (if the API is not enabled, then the requests will fail).
def Get_project_list():
    # get projects
    service_project     = discovery.build('cloudresourcemanager', 'v1', credentials=credentials)
    request_project     = service_project.projects().list()
    response_project    = request_project.execute()
    project_list        = response_project['projects']

    # an empty list to which we will add projects with enabled API
    projects_list_compute_enabled = []
    while request_project is not None:
        # for each project we check the list of its services and check if the Compute API is enabled
        for project in project_list:
            # if the project is not active, then we do not process the loop and check the next project
            if project["lifecycleState"] != "ACTIVE":
                continue
            # connect to GCP and get a list of all enabled services on the project
            project_id          = project["projectId"]
            parent              = "projects" + "/" + project_id 
            response_services   = discovery.build('serviceusage', 'v1', credentials=credentials).services().list(parent=parent,filter='state:ENABLED').execute()
            # by default, we assume that the Compute API is disabled
            COMPUTEAPI          = "NO"
            # if the Compute API is enabled, change the value of the COMPUTEAPI variable
            for s in response_services['services']:
                name_service    = s["name"].split("/")[-1]
                if "compute" in name_service:
                    COMPUTEAPI  = "YES"
                    break
            # if Compute API is enabled, add the project to the list
            if COMPUTEAPI == "YES":
                projects_list_compute_enabled.append(project_id)
        request_project = service_project.projects().list_next(previous_request=request_project, previous_response=response_project)
    return projects_list_compute_enabled


# create clusters in netbox, cluster == project.
def netbox_cluster_create():
    #### cluster group
    # if such a cluster group already exists, then update the tag and get the id
    if nb.virtualization.cluster_groups.filter(name= cluster_group_name):
        cluster_group       = nb.virtualization.cluster_groups.get(name = cluster_group_name)
        cluster_group.tags.append(tag_gcp_id)
        cluster_group.save()
        cluster_group_id    = cluster_group.id
    # if there is no cluster group, then create and get id
    else:
        cluster_group       = nb.virtualization.cluster_groups.create({"name": cluster_group_name, "slug": cluster_group_name, "tags":[{"name":tag_gcp_name, "slug":tag_gcp_name}]})
        cluster_group_id    = cluster_group.id

    ### cluster type
    # if this cluster type already exists, then update the tag and get its id
    if nb.virtualization.cluster_types.filter(name= cluster_type_name):
        cluster_type        = nb.virtualization.cluster_types.get(name= cluster_type_name)
        cluster_type.tags.append(tag_gcp_id)
        cluster_type.save()
        cluster_type_id     = cluster_type.id
    # if there is no cluster type, then create and get its id
    else:
        cluster_type        = nb.virtualization.cluster_types.create({"name": cluster_type_name, "slug": cluster_type_name, "tags":[{"name":tag_gcp_name, "slug":tag_gcp_name}]})
        cluster_type_id     = cluster_type.id

    ### add clusters == projects
    # get a list of all projects from an organization
    service_project         = discovery.build('cloudresourcemanager', 'v1', credentials=credentials)
    request_project         = service_project.projects().list()
    response_project        = request_project.execute()
    project_list            = response_project['projects']
    project_list_from_gcp   = []
    for project in project_list:
        project_name        = project["projectId"]
        project_list_from_gcp.append(project_name)
        # if such a cluster already exists, then update its group, type and tag in case they are out of date
        if nb.virtualization.clusters.filter(name = project_name):
            cluster         = nb.virtualization.clusters.get(name = project_name)
            cluster.type    = cluster_type_id
            cluster.group   = cluster_group_id
            cluster.tags.append(tag_gcp_id)
            cluster.save()
        # if there is no cluster, then create it and add the necessary group and type
        else:
            data_clusters   = {"name": project_name, "slug": project_name, "type" : cluster_type_id, "group" : cluster_group_id, "tags" : [{"name" : tag_gcp_name, "slug" : tag_gcp_name}]}
            nb.virtualization.clusters.create(**data_clusters)
    return(project_list_from_gcp)


# add data to global variables ip_from_gcp_list and ip_from_gcp_list. Up-to-date data on IP and VM
def Get_response_instances_from_project(project_id):
    # GCP connection
    service_compute = discovery.build('compute', 'v1', credentials=credentials)
    request         = service_compute.instances().aggregatedList(project=project_id)
    response        = request.execute()["items"].items()

    # an empty list to which VMs with parameters will be added
    instance_list   = []

    # we get set in response, in for will have two required parameters
    for zone, instanes in response:
        # parameters are returned for all zones, we are only interested in where the VM is
        if "instances" in instanes.keys():
            # create a list of VMs, write only the parameters we need
            for instance in instanes["instances"]:
                data_instance = {}

                # VM name
                data_instance["name"] = instance.get("name")

                # VM status
                if instance["status"] == "RUNNING":
                    STATUS  = "active"
                else:
                    STATUS  = "offline"
                data_instance["status"] = STATUS

                # interfaces
                if "networkInterfaces" in instance.keys():
                    for intf in instance["networkInterfaces"]:
                        # if VPC NOT default, then find Internal ip
                        if intf["network"].split("/")[-1] == "default":
                            pass
                        else:
                            ip_int                          = intf["networkIP"]
                            data_instance["ip_internal"]    = ip_int
                            # add IP in global var
                            ip_from_gcp_list["ip"].append(ip_int)
                        # external IP
                        if "accessConfigs" in intf.keys():
                            dict_accessConfigs = intf["accessConfigs"][0]
                            if "natIP" in dict_accessConfigs:
                                ip_ext                          = dict_accessConfigs["natIP"]
                                data_instance["ip_external"]    = ip_ext
                                # add IP in global var
                                ip_from_gcp_list["ip"].append(ip_ext)

                # disk size. summ all disks
                if "disks" in instance.keys():
                    summ_all_disks = 0
                    for disk in instance["disks"]:
                        summ_all_disks += int(disk["diskSizeGb"])
                    data_instance["disk"] = summ_all_disks

                # CPU and RAM
                if "machineType" in instance.keys():
                    instance_machine_type   = instance["machineType"].split("/")[-1]
                    request_machine_type    = service_compute.machineTypes().get(project=project_id, zone=zone.split("/")[-1], machineType=instance_machine_type)
                    response_machine_type   = request_machine_type.execute()
                    data_instance["memory"] = response_machine_type["memoryMb"]
                    data_instance["vcpus"]  = response_machine_type["guestCpus"]

                # add data for one VM to the VM list
                instance_list.append(data_instance)

    # add a list of VMs to a global variable with a dictionary for each project
    instance_list_per_project[project_id] = instance_list


# create VM and IP in netbox
def netbox_vm_create(project_id, data_instance):
    # VM parameters
    cluster         = nb.virtualization.clusters.get(name = project_id)
    cluster_id      = cluster.id
    name_vm         = data_instance["name"]
    status_vm       = data_instance["status"]
    disk_vm         = data_instance["disk"]
    memory_vm       = data_instance["memory"]
    vcpus_vm        = data_instance["vcpus"]
    # interfaces will always be created with the same name
    nic_internal    = "nic-internal"
    nic_external    = "nic-external"
    
    # new VM parameters
    data_new_vm     = {
        "name"      : name_vm,
        "status"    : status_vm,
        "cluster"   : cluster_id,
        "disk"      : disk_vm,
        "memory"    : memory_vm,
        "vcpus"     : vcpus_vm,
        "tags"      : [{"name":tag_gcp_name, "slug":tag_gcp_name}],
        }

    # if the VM already exists, then we update its parameters in case they are out of date, and we get the id. Because there may be VMs with the same names, then we are looking for in a specific cluster
    if nb.virtualization.virtual_machines.filter(name = name_vm, cluster = project_id):
        vm          = nb.virtualization.virtual_machines.get(name = name_vm, cluster = project_id)
        vm.status   = status_vm
        vm.disk     = disk_vm
        vm.memory   = memory_vm
        vm.vcpus    = vcpus_vm
        vm.tags.append(tag_gcp_id)
        vm.save()
        vm_id       = vm.id
    # if there is no VM, then we create it and get the id
    else:
        vm_new  = nb.virtualization.virtual_machines.create(**data_new_vm)
        vm_id   = vm_new.id
    
    # updating or creating internal interfaces
    if nb.virtualization.interfaces.filter(name = nic_internal, virtual_machine_id = vm_id):
        interface_internal_vm       = nb.virtualization.interfaces.get(name = nic_internal, virtual_machine_id = vm_id)
        interface_internal_vm.tags.append(tag_gcp_id)
        interface_internal_vm.save()
        interface_internal_vm_id    = interface_internal_vm.id
    else:
        data_interface_internal_vm  = {"name" : nic_internal, "virtual_machine" : vm_id, "tags" :[{"name":tag_gcp_name, "slug":tag_gcp_name}]}
        interface_internal_vm       = nb.virtualization.interfaces.create(**data_interface_internal_vm)
        interface_internal_vm_id    = interface_internal_vm.id

    # updating or creating external interfaces
    if nb.virtualization.interfaces.filter(name = nic_external, virtual_machine_id = vm_id):
        interface_external_vm       = nb.virtualization.interfaces.get(name = nic_external, virtual_machine_id = vm_id)
        interface_external_vm.tags.append(tag_gcp_id)
        interface_external_vm.save()
        interface_external_vm_id    = interface_external_vm.id
    else:
        data_interface_external_vm  = {"name" : nic_external, "virtual_machine" : vm_id, "tags" :[{"name":tag_gcp_name, "slug":tag_gcp_name}]}
        interface_external_vm       = nb.virtualization.interfaces.create(**data_interface_external_vm)
        interface_external_vm_id    = interface_external_vm.id

    # if there is an internal IP not from the default vpc in the VM parameters, then we create this IP and attach it to the VM
    if "ip_internal" in data_instance.keys():
        ip_internal                 = data_instance["ip_internal"]
        data_ip_internal_address    = {
                "address"               : ip_internal,
                "assigned_object_type"  : "virtualization.vminterface",
                "assigned_object_id"    : interface_internal_vm_id,
                "tags"                  : [{"name":tag_gcp_name, "slug":tag_gcp_name}]
            }
        if nb.ipam.ip_addresses.filter(address = ip_internal):
            ip_nb                       = nb.ipam.ip_addresses.get(address = ip_internal)
            ip_nb.assigned_object_type  = "virtualization.vminterface"
            ip_nb.assigned_object_id    = interface_internal_vm_id
            ip_nb.tags.append(tag_gcp_id)
            ip_nb.save()
        else:
            nb.ipam.ip_addresses.create(**data_ip_internal_address)
        # Add primary ip to VM
        try:
            vm_pimary_ip              = nb.virtualization.virtual_machines.get(name = name_vm, cluster = project_id)
            vm_pimary_ip.primary_ip4  = {"address" : ip_internal }
            vm_pimary_ip.save()
        except:
            print(f"error add primary IP to VM {name_vm}")
    else:
        pass

    # if there is a public IP in the VM parameters, then we create this IP and attach it to the VM
    if "ip_external" in data_instance.keys():
        ip_external                 = data_instance["ip_external"]
        data_ip_external_address    = {
            "address"               : ip_external,
            "assigned_object_type"  : "virtualization.vminterface",
            "assigned_object_id"    : interface_external_vm_id,
            "tags"                  : [{"name":tag_gcp_name, "slug":tag_gcp_name}]
        }
        if nb.ipam.ip_addresses.filter(address = ip_external):
            ip_nb                       = nb.ipam.ip_addresses.get(address = ip_external)
            ip_nb.assigned_object_type  = "virtualization.vminterface"
            ip_nb.assigned_object_id    = interface_external_vm_id
            ip_nb.tags.append(tag_gcp_id)
            ip_nb.save()
        else:
            nb.ipam.ip_addresses.create(**data_ip_external_address)
    else:
        pass


# delete obsolete clusters (projects), VM and IP
def delete_from_netbox(project_list_from_gcp,instance_list_per_project):
    # lists that we will fill in and compare
    instance_list_from_gcp      = []
    ip_list_from_gcp            = []
    project_from_netbox         = []
    instance_list_from_netbox   = []
    ip_list_from_netbox         = []

    # from the global variable instance_list_per_project we get a list of VMs and IPs from GCP
    for project in instance_list_per_project.keys():
        instance_list = instance_list_per_project[project]
        for instance in instance_list:
            instance_list_from_gcp.append(instance["name"])
            if "ip_internal" in instance.keys():
                ip_list_from_gcp.append(instance["ip_internal"])
            else:
                None
            if "ip_external" in instance.keys():
                ip_list_from_gcp.append(instance["ip_external"])
            else:
                None

    # get clusters (projects), VM and IP from netbox
    response_clusters_from_netbox = nb.virtualization.clusters.filter(tag = tag_gcp_name)
    for cluster in response_clusters_from_netbox:
        project_from_netbox.append(str(cluster))
    response_vm_from_netbox =  nb.virtualization.virtual_machines.filter(tag = tag_gcp_name)
    for instance in response_vm_from_netbox:
        instance_list_from_netbox.append(str(instance))
    response_ip_from_netbox =  nb.ipam.ip_addresses.filter(tag = tag_gcp_name)
    for ip in response_ip_from_netbox:
        # from netbox we get ip with a mask, remove it, because from GCP we receive without a mask. There must be one format for comparison
        ip_list_from_netbox.append(str(ip).split("/")[0])

    # compare lists and get those objects from netbox that do not match the current request from GCP
    list_project_difference = list(set(project_from_netbox) - set(project_list_from_gcp))
    list_vm_difference      = list(set(instance_list_from_netbox) - set(instance_list_from_gcp))
    list_ip_difference      = list(set(ip_list_from_netbox) - set(ip_list_from_gcp))

    # delete vm
    try:    
        for vm in list_vm_difference:
            del_vm = nb.virtualization.virtual_machines.get(name=vm, tag = tag_gcp_name)
            del_vm.delete()
    except:
        None

    # delete ip
    try:    
        for ip in list_ip_difference:
            del_ip = nb.ipam.ip_addresses.get(address=ip)
            del_ip.delete()
    except:
        None

    # delete cluster
    try:    
        for cluster in list_project_difference:
            del_cluster = nb.virtualization.clusters.get(name=cluster)
            del_cluster.delete()
    except:
        None


def main():
    # create cluster group, cluster type and clusters
    project_list_from_gcp = netbox_cluster_create()

    # get a list of projects with the compute API enabled
    projects_list_compute_enabled = Get_project_list()

    # we get the VM parameters for each project, add them to the global variable. We get internal IPs not from default vpc and public ips
    for project_id in projects_list_compute_enabled:
        Get_response_instances_from_project(project_id)

    # create VM and IP in netbox
    for project_id in instance_list_per_project.keys():
        instance_list = instance_list_per_project[project_id]
        for data_instance in instance_list:
            netbox_vm_create(project_id, data_instance)

    # delete irrelevant information from netbox
    delete_from_netbox(project_list_from_gcp,instance_list_per_project)


if __name__ == "__main__" :
    main()