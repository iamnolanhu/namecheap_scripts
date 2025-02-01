#!/usr/bin/env python3
"""
Add (or update) a subdomain A record at Namecheap via their public API.
Reads secrets/configs from .env, prompts for confirmation, and pushes DNS changes.
Includes:
  - Advanced TLD parsing via tldextract
  - Logging
  - Retries (3 max)
  - IP address default from .env
"""

# 1) Make sure you have a .env with the necessary variables 
#    (including NAMECHEAP_API_URL, NAMECHEAP_API_USER, etc.).

# 2) Then run:
# python add_subdomain.py --domain dev.example.com --ip 8.8.8.8

# Alternatively, omit --ip to use DEFAULT_IP from .env:
# python add_subdomain.py --domain staging.example.com

import os
import sys
import logging
import argparse
import xml.etree.ElementTree as ET
import requests
from dotenv import load_dotenv
import tldextract

# -------------------------------------------------------------------
# Logging Configuration
# -------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

def load_config():
    """
    Loads environment variables from .env and returns a dict.
    Raises ValueError if any required variable is missing.
    """
    load_dotenv()  # Load .env file
    
    config = {
        "API_URL":       os.getenv("NAMECHEAP_API_URL"),
        "API_USER":      os.getenv("NAMECHEAP_API_USER"),
        "API_KEY":       os.getenv("NAMECHEAP_API_KEY"),
        "USERNAME":      os.getenv("NAMECHEAP_USERNAME"),
        "CLIENT_IP":     os.getenv("NAMECHEAP_CLIENT_IP"),
        "DEFAULT_TTL":   os.getenv("DEFAULT_TTL", "1800"),  # fallback if not set
        "DEFAULT_IP":    os.getenv("DEFAULT_IP", "1.2.3.4"),# fallback if not set
    }
    
    # Check for missing environment variables
    missing = [k for k, v in config.items() if not v]
    if missing:
        raise ValueError(
            f"Missing required environment variables in .env: {missing}"
        )
    
    return config

def parse_domain_parts(domain_str):
    """
    Uses tldextract to parse domain strings, e.g.:
        'dev.example.com' -> subdomain='dev', sld='example', tld='com'.
    For multi-part TLDs (e.g. .co.uk), tldextract handles it properly.
    """
    ext = tldextract.extract(domain_str)
    
    subdomain = ext.subdomain  # Everything before the registered domain
    sld = ext.domain           # Registered domain name
    tld = ext.suffix           # The TLD (could be 'com', 'co.uk', etc.)
    
    if not sld or not tld:
        raise ValueError(
            f"Could not parse domain string '{domain_str}' into SLD/TLD. "
            "Please ensure you provided a valid domain."
        )
    
    # If no subdomain is provided (e.g., 'example.com'), subdomain will be ""
    if not subdomain:
        raise ValueError(
            "You must provide a subdomain. For example: sub.example.com"
        )
    
    return subdomain, sld, tld

def call_namecheap_api_with_retries(url, params, max_retries=3):
    """
    Wrapper to call the Namecheap API with GET and retry up to max_retries times
    in case of network errors or non-200 responses.
    """
    for attempt in range(1, max_retries + 1):
        try:
            logger.info(f"API call attempt {attempt}/{max_retries}: {params['Command']}")
            response = requests.get(url, params=params, timeout=30)
            
            if response.status_code == 200:
                return response
            else:
                logger.warning(
                    f"Received status code {response.status_code}, attempt {attempt}"
                )
        except requests.exceptions.RequestException as e:
            logger.warning(f"Request error on attempt {attempt}: {e}")
        
        # If not successful and not at max attempt, retry:
        if attempt < max_retries:
            logger.info("Retrying...")
        else:
            # Out of retries
            raise Exception(f"Failed after {max_retries} retries.")
    
    # Should never reach here:
    raise Exception("Unexpected exit from retry loop.")

def strip_namespaces(elem):
    """
    Recursively remove all namespaces from XML element tags, making
    it easier to do .find() or .findall() without worrying about XML namespaces.
    """
    for node in elem.iter():
        if '}' in node.tag:
            node.tag = node.tag.split('}', 1)[1]

def get_dns_records(config, sld, tld):
    """
    Fetch existing DNS records from Namecheap.
    Returns a list of dicts. Raises Exception if there's an error.
    """
    params = {
        "ApiUser":   config["API_USER"],
        "ApiKey":    config["API_KEY"],
        "UserName":  config["USERNAME"],
        "Command":   "namecheap.domains.dns.getHosts",
        "ClientIp":  config["CLIENT_IP"],
        "SLD":       sld,
        "TLD":       tld,
    }

    response = call_namecheap_api_with_retries(config["API_URL"], params)
    # Parse the XML
    root = ET.fromstring(response.text)
    # Strip namespaces
    strip_namespaces(root)

    # Check for errors
    errors = root.findall(".//Error")
    if errors:
        raise Exception(f"API Error: {errors[0].text}")

    # Gather host records
    hosts = []
    for host in root.findall(".//DomainDNSGetHostsResult/host"):
        hosts.append({
            "HostName":   host.get("Name"),
            "RecordType": host.get("Type"),
            "Address":    host.get("Address"),
            "MXPref":     host.get("MXPref", ""),
            "TTL":        host.get("TTL", config["DEFAULT_TTL"]),
        })

    return hosts

def set_dns_records(config, sld, tld, hosts):
    """
    Overwrites the DNS records with the given list of 'hosts' dicts.
    Raises Exception if the API call fails.
    """
    params = {
        "ApiUser":   config["API_USER"],
        "ApiKey":    config["API_KEY"],
        "UserName":  config["USERNAME"],
        "Command":   "namecheap.domains.dns.setHosts",
        "ClientIp":  config["CLIENT_IP"],
        "SLD":       sld,
        "TLD":       tld,
    }

    # Add each record to the query params
    for i, host in enumerate(hosts, start=1):
        params[f"HostName{i}"]   = host["HostName"]
        params[f"RecordType{i}"] = host["RecordType"]
        params[f"Address{i}"]    = host["Address"]
        params[f"MXPref{i}"]     = host["MXPref"] if host["MXPref"] else "10"
        params[f"TTL{i}"]        = host["TTL"] if host["TTL"] else config["DEFAULT_TTL"]

    # Make the Namecheap API call (with retries, etc.)
    response = call_namecheap_api_with_retries(config["API_URL"], params)

    # Parse the XML
    root = ET.fromstring(response.text)
    strip_namespaces(root)

    # Find the DomainDNSSetHostsResult element
    result = root.find(".//DomainDNSSetHostsResult")
    if (not result) or (result.get("IsSuccess") != "true"):
        raise Exception(f"Failed to update DNS records. Response: {response.text}")

def main():
    # -----------------------------------
    # 1) Parse CLI arguments
    # -----------------------------------
    parser = argparse.ArgumentParser(
        description="Add or update a subdomain A record in Namecheap DNS."
    )
    parser.add_argument(
        "--domain",
        required=True,
        help="Subdomain + domain, e.g. 'dev.example.com'"
    )
    parser.add_argument(
        "--ip",
        required=False,
        help="IP address for the A record (default: from DEFAULT_IP in .env)"
    )
    args = parser.parse_args()

    # -----------------------------------
    # 2) Load config from .env
    # -----------------------------------
    try:
        config = load_config()
    except ValueError as e:
        logger.error(f"Config error: {e}")
        sys.exit(1)

    # -----------------------------------
    # 3) Parse domain into subdomain, SLD, TLD
    # -----------------------------------
    try:
        subdomain, sld, tld = parse_domain_parts(args.domain)
    except ValueError as e:
        logger.error(e)
        sys.exit(1)

    # Determine final IP (use either CLI arg or default from .env)
    ip_address = args.ip if args.ip else config["DEFAULT_IP"]

    # -----------------------------------
    # Confirmation
    # -----------------------------------
    print(f"\nYou are about to add/update the following DNS record:")
    print(f"  Domain    : {sld}.{tld}")
    print(f"  Subdomain : {subdomain} (i.e., {subdomain}.{sld}.{tld})")
    print(f"  IP Address: {ip_address}\n")
    confirm = input("Do you want to proceed? (y/n): ").strip().lower()
    if confirm not in ["y", "yes"]:
        logger.info("Aborted by user.")
        sys.exit(0)

    # -----------------------------------
    # 4) Retrieve existing DNS records
    # -----------------------------------
    try:
        existing_records = get_dns_records(config, sld, tld)
        logger.info(f"Fetched {len(existing_records)} existing DNS record(s).")
    except Exception as e:
        logger.error(f"Could not retrieve DNS records: {e}")
        sys.exit(1)

    # -----------------------------------
    # 5) Create or update subdomain record
    # -----------------------------------
    # Remove old entries for the same subdomain if we want to ensure only one A record
    updated_records = [
        r for r in existing_records
        if not (r["HostName"] == subdomain and r["RecordType"] == "A")
    ]

    # Add the new record
    new_record = {
        "HostName":   subdomain,
        "RecordType": "A",
        "Address":    ip_address,
        "MXPref":     "",
        "TTL":        config["DEFAULT_TTL"],
    }
    updated_records.append(new_record)
    logger.info(
        f"Final DNS record set will have {len(updated_records)} record(s)."
    )

    # -----------------------------------
    # 6) Push updated records
    # -----------------------------------
    try:
        set_dns_records(config, sld, tld, updated_records)
        logger.info("DNS record updated successfully!")
    except Exception as e:
        logger.error(f"Failed to update DNS records: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
