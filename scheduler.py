import os
import sys
import time
from datetime import datetime
from dotenv import load_dotenv
import yaml
import json
import random
import kopf
from urllib.request import urlopen
from kubernetes import client, config
import asyncio

# load environment variables
load_dotenv()

# Read SCHEDULING_PERIOD from the environment
scheduling_period_str = os.getenv("SCHEDULING_PERIOD")

# Exit with error if SCHEDULING_PERIOD cannot be read
if scheduling_period_str is None:
    print("Error: SCHEDULING_PERIOD is not set in the .env file.", file=sys.stderr)
    sys.exit(1)  # Exit with error code 1

# Convert SCHEDULING_PERIOD to integer with base 10
scheduling_period = int(scheduling_period_str, 10)

# Configure the Kubernetes client
try:
    config.load_incluster_config()
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Using in-cluster configuration")
except config.config_exception.ConfigException:
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Falling back to kube-config")
    config.load_kube_config()

# Initialize the Kubernetes API client
v1 = client.CoreV1Api()

# Define global variables
global carbon_intensity_data, node_metadata_list


# Runs on Kopf startup and schedules the asynchronous
# execution of `run_scheduler_loop()`. It launches the workload scheduling
# loop in the background without blocking Kopf's event loop.
@kopf.on.startup()
async def startup_fn(**kwargs):
    asyncio.create_task(run_scheduler_loop(period=scheduling_period, total_run_count=180, is_carbon_aware=True))


# kopf handler which runs on each pod creation with the label kopf: true
@kopf.on.create('pods', labels={'kopf': 'true'})
def pod_handler(spec, meta, status, **kwargs):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] kopf pod_handler reacting to pod creation...")

    # Fetch all nodes
    nodes = v1.list_node().items

    # Extract node details
    node_details = []
    for node in nodes:
        labels = node.metadata.labels
        addresses = node.status.addresses
        ip_address = next((addr.address for addr in addresses if addr.type == "InternalIP"), None)
        node_details.append({
            "node": node.metadata.name,
            "region": labels.get("region"),
            "node_affinity": int(labels.get("node_affinity")),
            "carbon_intensity": float(labels.get("carbon_intensity")),
            "ip_address": ip_address,
        })

    # fetch workload name from pods metadata
    workload_name = meta['name']

    # Find the node with the highest node_affinity
    node_with_highest_affinity = max(
        node_details,
        key=lambda x: x["node_affinity"],
        default=None
    )

    # Get the IP address of the node, to which the pod is actually scheduled
    node_ip = status.get("hostIP", None)

    # from node_details chose the element which has ip_address equal to node_ip
    node_chosen_for_scheduling = next(
        (node for node in node_details if node["ip_address"] == node_ip),
        None
    )

    # set the workload name
    node_with_highest_affinity['workload'] = workload_name
    node_chosen_for_scheduling['workload'] = workload_name

    # Log the information
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] recommended pod placement: {node_with_highest_affinity}")
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] actual pod placement: {node_chosen_for_scheduling}")

    return


# labels node with the carbon affinity and node affinity in inverse relation
def label_nodes():
    # access global variables
    global carbon_intensity_data, node_metadata_list

    # Fetch the carbon emission data
    url = "https://wj38sqbq69.execute-api.us-east-1.amazonaws.com/Prod/row"
    response = urlopen(url)
    carbon_intensity_data = json.loads(response.read().decode('utf-8'))

    # fetch k8s nodes
    nodes = v1.list_node().items
    node_names = [node.metadata.name for node in nodes]

    # Sort regions by their carbon intensity (ascending)
    sorted_regions = sorted(carbon_intensity_data.items(), key=lambda item: item[1])
    affinity_values = [100, 50, 10]

    # Create the final list of objects with node_affinity in inverse relation with carbon_intensity
    node_metadata_list = [
        {"node": node, "region": region, "node_affinity": affinity, "carbon_intensity": carbon_intensity_data[region]}
        for (region, _), node, affinity in zip(sorted_regions, node_names, affinity_values)
    ]

    # Loop through the nodes in the cluster
    for obj in node_metadata_list:
        node_name = obj["node"]
        region = obj["region"]
        node_affinity = obj["node_affinity"]
        carbon_intensity = obj["carbon_intensity"]

        # Create the labels for the node under metadata
        body = {
            "metadata": {
                "labels": {
                    "region": region,
                    "node_affinity": str(node_affinity),
                    "carbon_intensity": str(carbon_intensity),
                }
            }
        }

        try:
            # Apply the body with the labels to the node
            response = v1.patch_node(node_name, body)
            filtered_labels = {key: response.metadata.labels[key] for key in
                               ["carbon_intensity", "kubernetes.io/hostname", "node_affinity", "region"] if
                               key in response.metadata.labels}

            print(
                f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Successfully labeled {node_name}: {filtered_labels}")
        except client.exceptions.ApiException as e:
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Error labeling {node_name}: {e}")


def set_pod_affinities(pod_definition):
    pod_definition["spec"].setdefault("affinity", {}).setdefault("nodeAffinity", {}).setdefault(
        "preferredDuringSchedulingIgnoredDuringExecution", [])
    pod_definition["spec"]["affinity"]["nodeAffinity"]["preferredDuringSchedulingIgnoredDuringExecution"] = [
        {
            "weight": 100,
            "preference": {
                "matchExpressions": [
                    {"key": "node_affinity", "operator": "In", "values": ["100"]}
                ]
            }
        },
        {
            "weight": 50,
            "preference": {
                "matchExpressions": [
                    {"key": "node_affinity", "operator": "In", "values": ["50"]}
                ]
            }
        },
        {
            "weight": 10,
            "preference": {
                "matchExpressions": [
                    {"key": "node_affinity", "operator": "In", "values": ["10"]}
                ]
            }
        }
    ]


async def schedule_workload(count: int, is_carbon_aware: bool):
    print('========================================================================')
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Scheduling workload {count}...")

    # label nodes with region, carbon_intensity, node_affinity
    label_nodes()

    # Load the workflow.yaml file
    with open("workload.yaml", "r") as workload_file:
        workload = yaml.safe_load(workload_file)

    # set unique pod name
    workload["metadata"]["name"] = f"workload-{count}"

    # set execution time randomly between 20-60 seconds
    execution_time = random.randint(20, 60)

    # set the randomly chosen time in the workload.yaml
    workload["spec"]["containers"][0]["args"] = [
        f"--time={execution_time}" if arg.startswith("--time=") else arg
        for arg in workload["spec"]["containers"][0]["args"]
    ]

    # set affinity preferences
    if is_carbon_aware:
        set_pod_affinities(pod_definition=workload)

    # Save the updated pod YAML to a new file
    new_file_name = f"workload-{count}.yaml"
    with open(new_file_name, "w") as file:
        yaml.safe_dump(workload, file, default_flow_style=False)

    try:
        # Deploy the pod using kubectl asynchronously
        process = await asyncio.create_subprocess_exec("kubectl", "apply", "-f", new_file_name)
        await process.communicate()
    finally:
        # Delete the temporary YAML file
        if os.path.exists(new_file_name):
            os.remove(new_file_name)


# Asynchronously schedules workloads at a fixed interval without blocking execution.
async def run_scheduler_loop(period, total_run_count, is_carbon_aware):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Started scheduling pods with a period of {period} seconds...")
    log_text = "Carbon aware" if is_carbon_aware else "Default"
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {log_text} scheduling.")
    run_count = 1
    while run_count <= total_run_count:
        asyncio.create_task(schedule_workload(count=run_count, is_carbon_aware=is_carbon_aware))
        await asyncio.sleep(period)  # Non-blocking sleep
        run_count += 1


# Start Kopf asynchronously
if __name__ == "__main__":
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Starting Kopf...")
    kopf.run()
