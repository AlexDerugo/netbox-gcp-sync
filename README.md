## Description
"netbox-gcp-sync" - this tag will be on all objects created with this script.  
IPs are added only public and internal NOT from default vpc.  
Work with GCP Organization. Object in netbox: GCP Organization - Cluster Group, GCP Project - Cluster.  
VM interfaces are always named nic-internal and nic-external. Because for a VM from GCP information about the real names of interfaces is irrelevant for us. nic-external is created so that there is a place to assign a public IP.  
!!! Important. The key to connect to the GCP must be located locally on the server. Format json exception set in gitignore.  

## How it work
- create clusters (projects) in netbox, the names are equal to the project ID, because project name can change
- get a list of projects with enabled compute API and only from the GCP organization
- get the VM parameters for each project with the compute API enabled, put them in a global variable. We get internal IP not from default vpc and public IP
- create VM and IP in netbox
- delete information from netbox
***