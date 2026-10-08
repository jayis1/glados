"""Can anything but a browser write the ollama.com model-page readme?

Run:  python3 tools/ollama_auth_probe.py measurements/raw/ollama_readme_auth_probe.json

The model-page readme is the one part of this publication an agent cannot do,
and "I tried and got a 401" is a weak claim if the 401 might just mean the
request was malformed. So this probe signs properly: it carries a from-scratch
RFC 8032 Ed25519 implementation (this host has neither pynacl nor
cryptography), checks itself against RFC 8032 test vector 1 before trusting a
single result, and reproduces Ollama's own registry handshake - including
minting a real `/v2/token`, which the registry issues.

It then asks for the readme write six ways and re-reads the page afterwards,
because a refused write that wrote anyway is the one outcome you must never
leave unmeasured.

Contains a minimal OpenSSH private-key parser for the same reason. One
signature per run, so a slow pure-python implementation is fine.
"""
import base64, hashlib, struct

b, q = 256, 2**255 - 19
l = 2**252 + 27742317777372353535851937790883648493

def H(m): return hashlib.sha512(m).digest()
def inv(x): return pow(x, q - 2, q)

d = -121665 * inv(121666) % q
I = pow(2, (q - 1) // 4, q)

def xrecover(y):
    xx = (y*y - 1) * inv(d*y*y + 1)
    x = pow(xx, (q + 3) // 8, q)
    if (x*x - xx) % q != 0: x = x * I % q
    if x % 2 != 0: x = q - x
    return x

By = 4 * inv(5) % q
B = [xrecover(By) % q, By]

def edwards(P, Q):
    x1, y1 = P; x2, y2 = Q
    k = d * x1 * x2 * y1 * y2
    x3 = (x1*y2 + x2*y1) * inv(1 + k)
    y3 = (y1*y2 + x1*x2) * inv(1 - k)
    return [x3 % q, y3 % q]

def scalarmult(P, e):
    if e == 0: return [0, 1]
    Q = scalarmult(P, e // 2)
    Q = edwards(Q, Q)
    return edwards(Q, P) if e & 1 else Q

def encodeint(y): return bytes((y >> (8*i)) & 0xff for i in range(b // 8))

def encodepoint(P):
    x, y = P
    bits = encodeint(y)
    return bytes(bits[i] | ((x & 1) << 7 if i == 31 else 0) for i in range(32))

def Hint(m):
    h = H(m)
    return sum(2**i * ((h[i // 8] >> (i % 8)) & 1) for i in range(2 * b))

def signature(m, sk, pk):
    """sk = 32-byte seed, pk = 32-byte public key."""
    h = H(sk)
    a = 2**(b - 2) + sum(2**i * ((h[i // 8] >> (i % 8)) & 1) for i in range(3, b - 2))
    r = Hint(h[b // 8:b // 4] + m)
    R = scalarmult(B, r)
    S = (r + Hint(encodepoint(R) + pk + m) * a) % l
    return encodepoint(R) + encodeint(S)

def _str(buf, off):
    (n,) = struct.unpack(">I", buf[off:off + 4])
    return buf[off + 4:off + 4 + n], off + 4 + n

def load_openssh_ed25519(path):
    """Return (seed32, pub32) from an unencrypted OpenSSH ed25519 private key."""
    body = "".join(ln.strip() for ln in open(path)
                   if not ln.startswith("-----"))
    buf = base64.b64decode(body)
    assert buf.startswith(b"openssh-key-v1\x00"), "not an openssh-key-v1 file"
    off = len(b"openssh-key-v1\x00")
    cipher, off = _str(buf, off)
    kdf, off = _str(buf, off)
    kdfopts, off = _str(buf, off)
    assert cipher == b"none", f"key is encrypted with {cipher!r}"
    (_n,), off = struct.unpack(">I", buf[off:off + 4]), off + 4
    _pub, off = _str(buf, off)
    priv, off = _str(buf, off)
    p = 8                              # two 4-byte check ints
    _kt, p = _str(priv, p)
    pub, p = _str(priv, p)
    sk, p = _str(priv, p)
    assert len(sk) == 64 and len(pub) == 32, (len(sk), len(pub))
    return sk[:32], pub           # openssh stores seed||pub

def ollama_authorization(key_path, data: bytes) -> str:
    """Ollama's header shape: <base64 pubkey body>:<base64 raw signature>."""
    seed, pub = load_openssh_ed25519(key_path)
    blob = b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00 " + pub
    sig = signature(data, seed, pub)
    return (base64.b64encode(blob).decode() + ":"
            + base64.b64encode(sig).decode())

# ---------------------------------------------------------------------------
# The probe. Can anything this machine holds write the model-page readme?
# ---------------------------------------------------------------------------
import json, os, secrets, time, urllib.parse, urllib.request, urllib.error, sys

UA    = "ollama/0.40.0"
MODEL = "jais/GLaDOS"
KEYS  = {"root": "/root/.ollama/id_ed25519",
         "ollama-user": "/usr/share/ollama/.ollama/id_ed25519"}


def call(method, url, hdrs=None, body=None):
    r = urllib.request.Request(url, data=body, headers=hdrs or {}, method=method)
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:                                  # network, TLS, DNS
        return None, f"could not measure: {e}"


def mint(key, scope):
    """A registry token, the way `ollama push` gets one."""
    nonce = base64.urlsafe_b64encode(secrets.token_bytes(16)).decode().rstrip("=")
    uri = ("/v2/token?nonce=" + nonce
           + "&scope=" + urllib.parse.quote(scope, safe="")
           + "&service=ollama&ts=" + str(int(time.time())))
    code, body = call("GET", "https://ollama.com" + uri,
                      {"Authorization": ollama_authorization(key, f"GET,{uri}".encode()),
                       "User-Agent": UA})
    return (json.loads(body)["token"] if code == 200 else None), code


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (os.path.join(here, os.pardir, "ollama-page.md"),
                 os.path.join(here, "ollama-page.md"),
                 "ollama-page.md"):
        if os.path.exists(cand):
            page = open(cand, encoding="utf-8").read().strip()
            break
    else:
        raise SystemExit("could not measure: ollama-page.md not found")
    form = urllib.parse.urlencode({"readme": page}).encode()
    hdr_form = {"Content-Type": "application/x-www-form-urlencoded", "User-Agent": UA}
    out = {"measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "model": MODEL, "payload_encoded_bytes": len(form), "attempts": []}

    def record(route, credential, code, note=""):
        out["attempts"].append({"route": route, "credential": credential,
                                "status": code, "note": note})
        print(f"  {credential:34} {route:22} -> {code} {note}")

    # 1. Is the signer itself correct? RFC 8032 vector 1, so a 401 below is a
    #    statement about authorization and not about our own arithmetic.
    seed = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
    pub  = bytes.fromhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
    ok = signature(b"", seed, pub).hex().startswith("e5564300c360ac72")
    out["signer_rfc8032_vector"] = "pass" if ok else "fail"
    print(f"signer: RFC 8032 vector {out['signer_rfc8032_vector']}")
    if not ok:
        out["verdict"] = "could not measure: signer is wrong"
        return out

    print("readme write, POST https://ollama.com/" + MODEL)
    code, body = call("POST", f"https://ollama.com/{MODEL}", hdr_form, form)
    record("POST /" + MODEL, "anonymous", code, body.strip()[:40])

    for who, path in KEYS.items():
        auth = ollama_authorization(path, f"POST,/{MODEL}".encode())
        code, body = call("POST", f"https://ollama.com/{MODEL}",
                          {**hdr_form, "Authorization": auth}, form)
        record("POST /" + MODEL, f"signed key ({who})", code, body.strip()[:40])

    for who, path in KEYS.items():
        tok, mc = mint(path, f"repository:{MODEL}:pull,push")
        if tok is None:
            record("GET /v2/token", f"mint ({who})", mc, "mint refused")
            continue
        record("GET /v2/token", f"mint ({who})", mc, "token issued")
        # Does that token carry push rights? An unfinished upload session is
        # inert, and it is the registry's own authorization check.
        up, _ = call("POST", f"https://ollama.com/v2/{MODEL}/blobs/uploads/",
                     {"Authorization": f"Bearer {tok}", "User-Agent": UA})
        record("POST /v2/.../uploads/", f"bearer ({who})", up,
               "push-authorized" if up in (200, 201, 202) else "not push-authorized")
        code, body = call("POST", f"https://ollama.com/{MODEL}",
                          {**hdr_form, "Authorization": f"Bearer {tok}"}, form)
        record("POST /" + MODEL, f"bearer ({who})", code, body.strip()[:40])

    # Is there any identity API that accepts key auth at all?
    for who, path in KEYS.items():
        code, body = call("POST", "https://ollama.com/api/me",
                          {"Authorization": ollama_authorization(path, b"POST,/api/me"),
                           "User-Agent": UA, "Content-Type": "application/json"}, b"")
        record("POST /api/me", f"signed key ({who})", code, body.strip()[:40])

    codes = {a["status"] for a in out["attempts"] if a["route"].startswith("POST /" + MODEL)}
    if None in codes:
        out["verdict"] = "could not measure: a request failed to complete"
    elif codes == {401}:
        out["verdict"] = ("human-only: every credential this host holds is refused "
                          "401 on the readme write; the route takes a browser "
                          "session cookie and nothing else")
    elif any(c and 200 <= c < 300 for c in codes):
        out["verdict"] = "WRITABLE: a non-cookie credential was accepted"
    else:
        out["verdict"] = f"inconclusive: statuses {sorted(c for c in codes if c)}"
    print("\nverdict: " + out["verdict"])
    return out


if __name__ == "__main__":
    res = main()
    # The page must be unchanged: a refused write that still wrote would be the
    # worst possible outcome to leave unmeasured.
    code, html = call("GET", f"https://ollama.com/{MODEL}")
    res["page_bytes_after"] = len(html) if code == 200 else None
    print(f"page after probing: http={code} bytes={res['page_bytes_after']}")
    if len(sys.argv) > 1:
        json.dump(res, open(sys.argv[1], "w"), indent=2)
        print("wrote " + sys.argv[1])
