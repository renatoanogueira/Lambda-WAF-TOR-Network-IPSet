import requests
import boto3
import json
from ipaddress import ip_address

# Settings
IPSET_NAME = "TOR-IPSet"            # Name of the IPSet created in WAF
IPSET_SCOPE = "CLOUDFRONT"          # Can be "REGIONAL" or "CLOUDFRONT"
AWS_REGION = "us-east-1"            # AWS region where the IPSet is

# Initialize the boto3 client for WAFv2
waf = boto3.client("wafv2", region_name=AWS_REGION)

# Function to fetch the list of exit nodes from TorBulkExitList
# This list contains only IPs, one per line

def fetch_torbulkexitlist():
    resp = requests.get("https://check.torproject.org/torbulkexitlist")
    resp.raise_for_status()
    return set(line.strip() for line in resp.text.splitlines() if line.strip())

# Function to fetch exit node IPs through the Onionoo API
# Returns a set of IPs listed in the 'exit_addresses' field

def fetch_onionoo_exit_addresses():
    exit_ips = set()
    url = "https://onionoo.torproject.org/details?flag=Exit&running=true"
    resp = requests.get(url)
    resp.raise_for_status()
    data = resp.json()
    for relay in data.get("relays", []):
        for ip in relay.get("exit_addresses", []):
            try:
                exit_ips.add(str(ip_address(ip)))
            except ValueError:
                continue  # Ignore invalid IPs
    return exit_ips

# Function that locates and returns an existing IPSet by name and scope

def get_ipset():
    next_marker = None
    while True:
        if next_marker:
            response = waf.list_ip_sets(Scope=IPSET_SCOPE, NextMarker=next_marker)
        else:
            response = waf.list_ip_sets(Scope=IPSET_SCOPE)
        for ipset_summary in response["IPSets"]:
            if ipset_summary["Name"] == IPSET_NAME:
                ipset_response = waf.get_ip_set(
                    Name=IPSET_NAME,
                    Scope=IPSET_SCOPE,
                    Id=ipset_summary["Id"]
                )
                ipset = ipset_response["IPSet"]
                ipset["LockToken"] = ipset_response["LockToken"]  # Add LockToken to dict
                return ipset
        if "NextMarker" in response:
            next_marker = response["NextMarker"]
        else:
            break
    raise Exception(f"IPSet '{IPSET_NAME}' not found.")

# Function to update the IPSet in AWS WAF
# Replaces the current set of IPs with the new combined set

def update_ipset(new_ips):
    ipset = get_ipset()
    current_ips = set(ipset["Addresses"])
    desired_ips = set(f"{ip}/32" for ip in new_ips)  # Format required by WAF

    if current_ips == desired_ips:
        print("No changes detected in the IPSet.")
        return

    response = waf.update_ip_set(
        Name=ipset["Name"],
        Scope=IPSET_SCOPE,
        Id=ipset["Id"],
        LockToken=ipset["LockToken"],
        Addresses=sorted(list(desired_ips))
    )
    print("IPSet updated successfully.")

# Main function: collects the lists, deduplicates, compares and updates

def main():
    try:
        print("Getting Tor network IP lists...")
        torbulk_ips = fetch_torbulkexitlist()
        onionoo_ips = fetch_onionoo_exit_addresses()

        combined_ips = torbulk_ips.union(onionoo_ips)
        print(f"Total combined IPs: {len(combined_ips)}")
        print("Example of combined IPs:")
        for ip in list(sorted(combined_ips))[:20]:  # Show the first 20 to avoid cluttering the terminal
            print(ip)
        # print(sorted(combined_ips))  # Uncomment if you want to see all

        update_ipset(combined_ips)  # Now the update will actually be performed!
    except Exception as e:
        print(f"Error during execution: {e}")

if __name__ == "__main__":
    main()
