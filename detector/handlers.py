"""
Per-type handlers for resource types whose comparison is structural (lists of
objects) rather than scalar attribute diffs — the cases config + snake->camel
mapping cannot express.

Contract: a handler takes (spec, state_attrs, boto3_clients) and returns a
drift list of (name, declared, actual) triples — the SAME shape the scalar
engine produces — so the dedup / DynamoDB / dashboard downstream is unchanged.

Registry at the bottom maps a spec's `handler` name to its function.
"""

from botocore.exceptions import ClientError


# ---------------------------------------------------------------------------
# security group: ingress + egress rules (IPv4 CIDR only), plus vpc_id (scalar)
# ---------------------------------------------------------------------------
def _canon_state_rules(rule_blocks):
    """
    Canonicalize state-side inline rule blocks into a set of hashable tuples.
    Each block may carry multiple cidr_blocks -> expand to one tuple per CIDR.
    Identity = (protocol, from_port, to_port, cidr).  Description ignored.
    Scope: IPv4 cidr_blocks only; sg-refs / ipv6 / prefix-lists excluded.
    """
    out = set()
    for r in rule_blocks or []:
        proto = str(r.get("protocol"))
        if proto == "-1":            # skip all-traffic rules (out of scope)
            continue
        frm   = r.get("from_port")
        to    = r.get("to_port")
        for cidr in (r.get("cidr_blocks") or []):
            out.add((proto, frm, to, cidr))
    return out


def _canon_live_rules(ip_permissions):
    """
    Canonicalize live describe_security_groups IpPermissions[] into the same
    tuple set. Live nests CIDRs under IpRanges:[{CidrIp}]; protocol '-1' means
    all, and from/to may be absent for all-traffic rules.
    """
    out = set()
    for p in ip_permissions or []:
        proto = str(p.get("IpProtocol"))
        if proto == "-1":            # skip all-traffic rules (out of scope)
            continue
        frm   = p.get("FromPort")
        to    = p.get("ToPort")
        for rng in (p.get("IpRanges") or []):
            cidr = rng.get("CidrIp")
            if cidr is not None:                 # IPv4 only; Ipv6Ranges ignored
                out.add((proto, frm, to, cidr))
    return out


def _rule_str(t):
    proto, frm, to, cidr = t
    port = "all" if frm is None else (str(frm) if frm == to else f"{frm}-{to}")
    proto = "all" if proto == "-1" else proto
    return f"{proto}/{port}/{cidr}"


def security_group(spec, state_attrs, boto3_clients):
    client = boto3_clients.get(spec.get("resource_type"))
    if client is None:
        raise RuntimeError(f"No boto3 client for {spec.get('resource_type')!r}")

    sg_id = state_attrs.get(spec["id_attribute"])
    try:
        resp = client.describe_security_groups(GroupIds=[sg_id])
        live_sg = resp["SecurityGroups"][0]
    except (ClientError, IndexError, KeyError) as e:
        print(f"⚠️ SG live fetch failed for {sg_id}: {e}")
        # whole thing absent -> report every declared rule as removed + vpc drift
        return [("security_group", "present", "<absent>")]

    drift = []

    # --- vpc_id (scalar) ---
    declared_vpc = state_attrs.get("vpc_id")
    actual_vpc   = live_sg.get("VpcId")
    if declared_vpc != actual_vpc:
        drift.append(("vpc_id", declared_vpc, actual_vpc))

    # --- ingress rules (set diff) ---
    dec_in = _canon_state_rules(state_attrs.get("ingress"))
    act_in = _canon_live_rules(live_sg.get("IpPermissions"))
    for t in sorted(act_in - dec_in):            # in live, not declared -> added
        drift.append(("ingress_rule_added", None, _rule_str(t)))
    for t in sorted(dec_in - act_in):            # declared, not live -> removed
        drift.append(("ingress_rule_removed", _rule_str(t), None))

    # --- egress rules (set diff) ---
    dec_eg = _canon_state_rules(state_attrs.get("egress"))
    act_eg = _canon_live_rules(live_sg.get("IpPermissionsEgress"))
    for t in sorted(act_eg - dec_eg):
        drift.append(("egress_rule_added", None, _rule_str(t)))
    for t in sorted(dec_eg - act_eg):
        drift.append(("egress_rule_removed", _rule_str(t), None))

    return drift


HANDLERS = {
    "security_group": security_group,
}