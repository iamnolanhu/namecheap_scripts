#!/usr/bin/env python3
"""
Check existing DNS records for a given domain on Namecheap and print them.
Requires environment variables in .env (NAMECHEAP_API_URL, NAMECHEAP_API_USER, etc.).
"""

import os
import sys
import logging
import argparse
import xml.etree.ElementTree as ET
import requests
from dotenv import load_dotenv
import tldextract

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
        "API_URL":   os.getenv("NAMECHEAP_API_URL"),
        "API_USER":  os.getenv("NAMECHEAP_API_USER"),
        "API_KEY":   os.getenv("NAMECHEAP_API_KEY"),
        "USERNAME":  os.getenv("NAMECHEAP_USERNAME"),
        "CLIENT_IP": os.getenv("NAMECHEAP_CLIENT_IP"),
    }
    
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
    """
    ext = tldextract.extract(domain_str)
    subdomain = ext.subdomain
    sld = ext.domain
    tld = ext.suffix
    
    if not sld or not tld:
        raise ValueError(
            f"Could not parse domain string '{domain_str}'. "
            "Please ensure it's a valid domain."
        )
    
    return subdomain, sld, tld

def call_namecheap_api_with_retries(url, params, max_retries=3):
    """
    Makes a GET request to Namecheap's API, retrying up to max_retries times on failure.
    """
    for attempt in range(1, max_retries + 1):
        try:
            logger.info(f"API call attempt {attempt}/{max_retries}: {params['Command']}")
            response = requests.get(url, params=params, timeout=30)
            if response.status_code == 200:
                return response
            else:
                logger.warning(f"Non-200 status code {response.status_code}, attempt {attempt}")
        except requests.exceptions.RequestException as e:
            logger.warning(f"Request error on attempt {attempt}: {e}")
        
        if attempt < max_retries:
            logger.info("Retrying...")
        else:
            raise Exception(f"Failed after {max_retries} retries.")
    
    raise Exception("Unexpected exit from retry loop.")

def strip_namespaces(elem):
    """
    Recursively remove all namespaces from XML element tags, so we can do
    root.findall('.//DomainDNSGetHostsResult/host') without worrying about namespaces.
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
    
    # DEBUG: Print out the raw XML response to see if it includes a message or any data
    logger.debug(f"Raw API response:\n{response.text}")

    # Parse the XML
    root = ET.fromstring(response.text)
    # Remove default namespaces so we can reliably run 'findall'
    strip_namespaces(root)

    errors = root.findall(".//Error")
    if errors and len(errors) > 0 and errors[0].text:
        raise Exception(f"API Error: {errors[0].text}")

    hosts = []
    # Now we can safely find the <host> elements without dealing with namespace prefixes:
    for host in root.findall(".//DomainDNSGetHostsResult/host"):
        hosts.append({
            "HostName":   host.get("Name"),
            "RecordType": host.get("Type"),
            "Address":    host.get("Address"),
            "MXPref":     host.get("MXPref", ""),
            "TTL":        host.get("TTL"),
        })

    return hosts

def main():
    parser = argparse.ArgumentParser(
        description="Fetch and display all DNS records for a given Namecheap domain."
    )
    parser.add_argument(
        "--domain",
        required=True,
        help="Full domain name (with or without subdomain). e.g. 'dev.example.com'"
    )
    parser.add_argument(
        "--log",
        default="DEBUG",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Set the logging level (default: INFO)."
    )
    args = parser.parse_args()
    
    # Set up logging based on the --log argument
    logging.basicConfig(
        level=args.log,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    global logger
    logger = logging.getLogger(__name__)

    # 1) Load environment config
    try:
        config = load_config()
    except ValueError as e:
        logger.error(f"Config error: {e}")
        sys.exit(1)
    
    # 2) Parse domain
    try:
        subdomain, sld, tld = parse_domain_parts(args.domain)
        logger.info(f"Parsed domain: subdomain='{subdomain}', sld='{sld}', tld='{tld}'")
    except ValueError as e:
        logger.error(e)
        sys.exit(1)

    # 3) Fetch existing records
    try:
        records = get_dns_records(config, sld, tld)
    except Exception as e:
        logger.error(f"Failed to fetch DNS records: {e}")
        sys.exit(1)

    # 4) Print them out
    print("\nExisting DNS Records:")
    if not records:
        print("No records returned.")
    else:
        for i, rec in enumerate(records, start=1):
            print(f"{i}. HostName: {rec['HostName']}")
            print(f"   Type    : {rec['RecordType']}")
            print(f"   Address : {rec['Address']}")
            print(f"   MXPref  : {rec['MXPref']}")
            print(f"   TTL     : {rec['TTL']}")
            print("")
    print("Done. (No changes were made.)")

if __name__ == "__main__":
    main() 