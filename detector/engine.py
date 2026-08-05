"""
Config-driven comparison engine.
Reads config.yaml, scans the Terraform state
file for every configured aspect type, and for each instance found produces a
normalized (resource_id, bucket, aspect, drift) result that feeds the EXISTING
dedup state machine unchanged.

Nothing here talks to DynamoDB / SNS / Slack — this only produces drift facts.
"""

import json
import os

import boto3
import yaml

from botocore.exceptions import ClientError
from handlers import HANDLERS
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass   

STATE_BUCKET = os.getenv("STATE_BUCKET", "drift-detector-tfstate-anushka")
STATE_KEY    = os.getenv("STATE_KEY",    "drift-detector/terraform.tfstate")
CONFIG_PATH  = os.getenv("CONFIG_PATH",
                         os.path.join(os.path.dirname(__file__), "config.yaml"))

s3 = boto3.client("s3")

def get_unique_resource_types(config):
    """Return a set of all resource_type values in the config."""
    tt={spec.get("resource_type", "unknown") for spec in config.values()}
    print(tt)
    return tt

def load_clients():
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)["clients"]

def initialize_boto3_clients(resource_types):
    """Initialize boto3 clients for S3, DynamoDB, and SNS."""
    global boto3_clients
    boto3_clients = dict()
    client_yml= load_clients()
    for resource_type in resource_types:
        if client_yml.get(resource_type,None) is not None:
            method=getattr(boto3, client_yml[resource_type]["client"])
            boto3_clients[resource_type] = method(client_yml[resource_type]["service"])
        else:
            print(f'⚠️ No client config for resource type: {resource_type}')
    print(f"Initialized boto3 clients for resource types: {list(boto3_clients.keys())}")
    

# ---------------------------------------------------------------------------
# name mapping: state (snake_case) -> live (PascalCase)
# ---------------------------------------------------------------------------
def snake_to_pascal(name):
    """block_public_acls -> BlockPublicAcls. Naive; overridable per attribute."""
    return "".join(part.capitalize() for part in name.split("_"))


def normalize_attributes(raw_attrs):
    """
    Config `attributes` may be either a bare string (auto-map) or a dict with
    an explicit `live:` override. Return a uniform list of
    {"state": <snake>, "live": <pascal>}.
    """
    out = []
    for a in raw_attrs:
        if isinstance(a, str):
            out.append({"state": a, "live": snake_to_pascal(a), "state_path": a})
        else:                                   
            state = a["name"]
            out.append({"state": state, "live": a.get("live", snake_to_pascal(state)), "state_path": a.get("state_path", state)})
    return out


def dig(obj, path):
    """Walk a dotted path: 'versioning_configuration.0.status'.
    Integer segments index lists; others key dicts. Returns None if any step misses."""
    cur = obj
    for seg in path.split("."):
        if isinstance(cur, list):
            try:
                cur = cur[int(seg)]
            except (ValueError, IndexError):
                return None
        elif isinstance(cur, dict):
            cur = cur.get(seg)
        else:
            return None
        if cur is None:
            return None
    return cur

# ---------------------------------------------------------------------------
# load config + state
# ---------------------------------------------------------------------------
def load_config():
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)["resource_types"]


def load_state():
    obj = s3.get_object(Bucket=STATE_BUCKET, Key=STATE_KEY)
    return json.loads(obj["Body"].read())


def find_instances(state, state_type):
    """Every instance of a given resource type in the state file."""
    found = []
    for res in state.get("resources", []):
        if res.get("type") == state_type:
            for inst in res.get("instances", []):
                found.append(inst.get("attributes", {}))
    print(f"Found {len(found)} instances of {state_type} in state file.")
    print(found)
    return found


# ---------------------------------------------------------------------------
# per-aspect live fetch
# ---------------------------------------------------------------------------
def fetch_live(spec, resource_name):
    """
    Call the boto3 method named in spec['api'] on the s3 client, pass the bucket,
    and dig out the value dict via spec['response_key'] (or the response root).
    Returns None if the live resource is absent (e.g. never configured).
    """
    botoclient = boto3_clients.get(spec.get("resource_type"))
    if botoclient is None:
        raise RuntimeError(f"No boto3 client for resource_type {spec.get('resource_type')!r} — check config `clients`")
    method = getattr(botoclient, spec["api"])
    arg = [resource_name] if spec.get("param_style") == "list" else resource_name
    try:
        resp = method(**{spec["parameter"]: arg})
    except ClientError as e:
        print(f"⚠️ Live fetch failed for {resource_name} ({spec['api']}): {e}")
        return None  
    if spec.get("response_path"):                 # EC2: nested path
        return dig(resp, spec["response_path"])                            # e.g. no public-access-block set
    rkey = spec.get("response_key")
    return resp.get(rkey, {}) if rkey else resp


# ---------------------------------------------------------------------------
# compare one instance
# ---------------------------------------------------------------------------
def compare_instance(spec, state_attrs):
    """
    Returns (resource_id, resource_name, aspect, drift_list). drift_list entries are
    (attribute_name, declared_value, actual_value) 
    """
    resource_name = state_attrs[spec["id_attribute"]]
    aspect = spec["_aspect_name"]
    resource_id = f"{resource_name}::{aspect}"
    resource_type = spec.get("resource_type", "unknown")
    handler_name = spec.get("handler")
    if handler_name:
        handler = HANDLERS.get(handler_name)
        if handler is None:
            raise RuntimeError(f"Unknown handler {handler_name!r} for {resource_id}")
        drift = handler(spec, state_attrs, boto3_clients)
        return resource_id, resource_name, aspect, resource_type, drift
    attrs = normalize_attributes(spec["attributes"])
    live = fetch_live(spec, resource_name)
    print(f"Comparing {resource_id}: declared={state_attrs}, actual={live}")
    drift = []
    if live is None:
        # resource is declared in state but absent live -> whole thing drifted
        for a in attrs:
            drift.append((a["state"], state_attrs.get(a["state"]), "<absent>"))
        return resource_id, resource_name, aspect,resource_type, drift

    for a in attrs:
        declared = dig(state_attrs, a["state_path"])
        actual   = live.get(a["live"])
        if declared != actual:
            print(f"⚠️ Drift detected for {resource_id}: {a['state']} (declared={declared}, actual={actual})")
            drift.append((a["state"], declared, actual))
    return resource_id, resource_name, aspect, resource_type, drift


# ---------------------------------------------------------------------------
# top-level: yield every monitored instance's result
# ---------------------------------------------------------------------------
def evaluate_all():
    """
    Yields one (resource_id, resource_name, aspect, resource_type, drift) per monitored instance
    across all configured aspect types. drift == [] means in-sync.
    """
    config = load_config()
    state = load_state()
    initialize_boto3_clients(get_unique_resource_types(config))

    for aspect_name, spec in config.items():
        spec = dict(spec)                        # don't mutate the loaded config
        spec["_aspect_name"] = aspect_name
        for state_attrs in find_instances(state, spec["state_type"]):
            yield compare_instance(spec, state_attrs)