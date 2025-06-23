import requests
import boto3
import json
import os
from ipaddress import ip_address

# Main IPSet and AWS settings
IPSET_NAME = "Tor-IPSet"
IPSET_SCOPE = "CLOUDFRONT"  # or "REGIONAL" if not CloudFront
AWS_REGION = "us-east-1"    # Required region for CLOUDFRONT

# Initialize boto3 clients for WAFv2 and SNS
waf = boto3.client("wafv2", region_name=AWS_REGION)
sns = boto3.client("sns")

# Read the SNS topic ARN from the environment variable
SNS_TOPIC_ARN = os.getenv("SNS_TOPIC_ARN")

# Fetch the list of exit node IPs from TorBulkExitList
def fetch_torbulkexitlist():
    resp = requests.get("https://check.torproject.org/torbulkexitlist")
    resp.raise_for_status()  # Raises exception if the request fails
    # Returns a set of IPs, one per line
    return set(line.strip() for line in resp.text.splitlines() if line.strip())

# Fetch exit node IPs via Onionoo API
def fetch_onionoo_exit_addresses():
    exit_ips = set()
    url = "https://onionoo.torproject.org/details?flag=Exit&running=true"
    resp = requests.get(url)
    resp.raise_for_status()
    data = resp.json()
    for relay in data.get("relays", []):
        for ip in relay.get("exit_addresses", []):
            try:
                exit_ips.add(str(ip_address(ip)))  # Validate and add IP
            except ValueError:
                continue  # Ignore invalid IPs
    return exit_ips

# Locate and return the existing IPSet by name and scope, including the LockToken
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
                ipset["LockToken"] = ipset_response["LockToken"]
                return ipset
        if "NextMarker" in response:
            next_marker = response["NextMarker"]
        else:
            break
    raise Exception(f"🚨 WAF - IPSet '{IPSET_NAME}' not found.")

# Update the IPSet in AWS WAF, replacing the current IPs with the new set
def update_ipset(new_ips):
    ipset = get_ipset()
    current_ips = set(ipset["Addresses"])
    desired_ips = set(f"{ip}/32" for ip in new_ips)  # Format required by WAF

    if current_ips == desired_ips:
        print("No changes detected in the IPSet.")
        return False, len(desired_ips)  # No update, but success

    # Update the IPSet in AWS
    waf.update_ip_set(
        Name=ipset["Name"],
        Scope=IPSET_SCOPE,
        Id=ipset["Id"],
        LockToken=ipset["LockToken"],
        Addresses=sorted(list(desired_ips))
    )
    print("IPSet updated successfully.")
    return True, len(desired_ips)  # Update done

# Lambda main function
def lambda_handler(event, context):
    result_message = ""
    try:
        print("Getting Tor network IP lists...")
        torbulk_ips = fetch_torbulkexitlist()           # Fetch TorBulk list
        onionoo_ips = fetch_onionoo_exit_addresses()    # Fetch Onionoo list
        combined_ips = torbulk_ips.union(onionoo_ips)   # Combine and deduplicate IPs
        print(f"Total combined IPs: {len(combined_ips)}")

        updated, ip_count = update_ipset(combined_ips)  # Update IPSet
        if updated:
            result_message = f"✅ The IPSet '{IPSET_NAME}' was updated and now has {ip_count} IPs."
        else:
            result_message = f"👍 The IPSet '{IPSET_NAME}' did not need updating. Still has ({ip_count} IPs)."
    except Exception as e:
        result_message = f"Error during execution: {e}"
        if SNS_TOPIC_ARN:
            sns.publish(
                TopicArn=SNS_TOPIC_ARN,
                Subject="❌ Error in Lambda WAF execution - IPSet TOR Network Update",
                Message=result_message
            )
        print(result_message)
        raise  # Ensures the error is logged as a Lambda failure
    else:
        # Only notify success if there was no error
        if SNS_TOPIC_ARN:
            sns.publish(
                TopicArn=SNS_TOPIC_ARN,
                Subject="🔔 Lambda WAF execution result - IPSet TOR Network Update",
                Message=result_message
            )
