import json
import time
import urllib.request
import urllib.error
import os
import base58
from collections import defaultdict

# Free Helius API key can be obtained at https://dashboard.helius.dev/api-keys
BASE_RPC_ENDPOINTS = [
    'https://api.mainnet-beta.solana.com',
    'https://solana-api.projectserum.com',
    'https://rpc.ankr.com/solana',
]

def _get_rpc_endpoints():
    key = os.environ.get('HELIUS_API_KEY', '')
    if key:
        return [f'https://mainnet.helius-rpc.com/?api-key={key}'] + BASE_RPC_ENDPOINTS
    return BASE_RPC_ENDPOINTS

class RateLimitError(Exception):
    pass

def _rpc_call(method, params, endpoint=None):
    endpoints = [endpoint] if endpoint else _get_rpc_endpoints()
    last_err = None
    for ep in endpoints:
        try:
            data = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}).encode()
            req = urllib.request.Request(ep, data, {'Content-Type': 'application/json'})
            try:
                with urllib.request.urlopen(req, timeout=15) as resp:
                    result = json.loads(resp.read())
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    raise RateLimitError('Rate limited (429)')
                raise
            if result is None:
                raise Exception('RPC returned null')
            if 'error' in result:
                err = result['error']
                err_str = str(err).lower()
                if 'rate limit' in err_str or 'too many requests' in err_str or '429' in err_str:
                    raise RateLimitError(str(err))
                raise Exception(err)
            return result['result']
        except RateLimitError:
            raise
        except Exception as e:
            last_err = e
            time.sleep(0.1)
    raise last_err

def get_signatures(address, limit=50):
    try:
        return _rpc_call('getSignaturesForAddress', [address, {'limit': limit}])
    except:
        return []

def get_transaction(sig):
    try:
        return _rpc_call('getTransaction', [sig, {'encoding': 'json', 'maxSupportedTransactionVersion': 0}])
    except:
        return None

def get_balance(address):
    try:
        return _rpc_call('getBalance', [address])
    except:
        return {'value': 0}

def _decode_base58(s):
    """Decode base58 string to bytes."""
    alphabet = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
    n = 0
    for c in s:
        n = n * 58 + alphabet.index(c)
    return n.to_bytes((n.bit_length() + 7) // 8, 'big') or b'\x00'

SYSTEM_PROGRAM = '11111111111111111111111111111111'
TOKEN_PROGRAM = 'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA'

def parse_transfers(tx, sig):
    """Extract SOL and SPL transfers from a transaction using instruction parsing."""
    transfers = []
    meta = tx.get('meta')
    if not meta:
        return transfers

    tx_json = tx.get('transaction', {})
    msg = tx_json.get('message', {})
    accts = msg.get('accountKeys', [])
    idx_to_pub = {i: a.get('pubkey', a) if isinstance(a, dict) else a for i, a in enumerate(accts)}
    prog_idx_to_pub = {i: idx_to_pub.get(a) for i, a in enumerate(msg.get('accountKeys', []))}
    time_str = time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(tx.get('blockTime', 0)))
    fee = meta.get('fee', 0) / 1e9
    fee_payer = idx_to_pub.get(0, '') if accts else ''

    # Collect all instructions (top-level + inner)
    all_instructions = []
    for instr in msg.get('instructions', []):
        all_instructions.append(instr)
    for inner_group in meta.get('innerInstructions', []):
        all_instructions.extend(inner_group.get('instructions', []))

    for instr in all_instructions:
        prog_idx = instr.get('programIdIndex', -1)
        prog_id = prog_idx_to_pub.get(prog_idx, '')
        acc_idxs = instr.get('accounts', [])
        data = instr.get('data', '')

        # System Program transfer (index 2)
        if prog_id == SYSTEM_PROGRAM and len(acc_idxs) >= 2:
            try:
                raw = _decode_base58(data)
                if len(raw) > 0 and raw[0] == 2:  # System transfer instruction
                    from_pub = idx_to_pub.get(acc_idxs[0], '')
                    to_pub = idx_to_pub.get(acc_idxs[1], '')
                    if from_pub and to_pub:
                        amount = int.from_bytes(raw[1:9], 'little') / 1e9
                        if amount >= 0.000001:
                            transfers.append({'from': from_pub, 'to': to_pub, 'amount': round(amount, 9), 'unit': 'SOL', 'time': time_str, 'sig': sig[:13]})
            except:
                pass

        # SPL Token transfer (index 3) or transferChecked (index 12)
        elif prog_id == TOKEN_PROGRAM and len(acc_idxs) >= 3:
            try:
                raw = _decode_base58(data)
                if len(raw) > 0 and raw[0] in (3, 12):  # transfer or transferChecked
                    source = idx_to_pub.get(acc_idxs[0], '')
                    dest = idx_to_pub.get(acc_idxs[1], '')
                    if source and dest:
                        if raw[0] == 3:
                            amount = int.from_bytes(raw[1:9], 'little') / (10 ** 6)
                        else:
                            amount = int.from_bytes(raw[1:9], 'little') / (10 ** (raw[9] if len(raw) > 9 else 6))
                        unit = f'TOKEN'
                        if amount >= 0.0001:
                            transfers.append({'from': source, 'to': dest, 'amount': round(amount, 6), 'unit': unit, 'time': time_str, 'sig': sig[:13]})
            except:
                pass

    # If no transfers found via instructions, use balance-based fallback
    if not any(t for t in transfers if t['unit'] == 'SOL'):
        pre_balances = meta.get('preBalances', [])
        post_balances = meta.get('postBalances', [])
        sol_changes = []
        for i in range(len(idx_to_pub)):
            if i >= len(pre_balances) or i >= len(post_balances):
                continue
            diff = (post_balances[i] - pre_balances[i]) / 1e9
            if abs(diff) < 0.00001:
                continue
            pub = idx_to_pub.get(i, '')
            if pub == fee_payer and abs(diff + fee) < 0.00001:
                continue  # Skip fee payer's fee
            sol_changes.append({'pub': pub, 'diff': round(diff, 9)})

        # Try to pair one sender with one receiver
        senders = [s for s in sol_changes if s['diff'] < -0.00001]
        receivers = [r for r in sol_changes if r['diff'] > 0.00001]
        for s in senders:
            remaining = -s['diff']
            for r in receivers:
                if remaining <= 0:
                    break
                if r['diff'] <= 0:
                    continue
                take = min(remaining, r['diff'])
                if take >= 0.00001:
                    transfers.append({'from': s['pub'], 'to': r['pub'], 'amount': round(take, 9), 'unit': 'SOL', 'time': time_str, 'sig': sig[:13]})
                    remaining = round(remaining - take, 9)
                    r['diff'] = round(r['diff'] - take, 9)

    # Token balance based transfers (always run, not just fallback)
    pre_token = meta.get('preTokenBalances', [])
    post_token = meta.get('postTokenBalances', [])
    if pre_token or post_token:
        def get_token_amt(t):
            info = t.get('uiTokenAmount', {})
            ui = info.get('uiAmount')
            if ui is not None:
                return float(ui)
            raw = info.get('amount', '0')
            dec = info.get('decimals', 0)
            try:
                return int(raw) / (10 ** dec)
            except:
                return 0.0

        # Build pre/post maps per ATA
        pre_map = {}
        for t in pre_token:
            acct = idx_to_pub.get(t.get('accountIndex', -1), '')
            mint = t.get('mint', '')
            amt = round(get_token_amt(t), 6)
            owner = t.get('owner', '')
            pre_map[(acct, mint, owner)] = amt

        post_map = {}
        for t in post_token:
            acct = idx_to_pub.get(t.get('accountIndex', -1), '')
            mint = t.get('mint', '')
            amt = round(get_token_amt(t), 6)
            owner = t.get('owner', '')
            post_map[(acct, mint, owner)] = amt

        # Calculate per-owner per-mint changes
        all_owners = set()
        all_mints = set()
        for acct, mint, owner in list(pre_map.keys()) + list(post_map.keys()):
            if owner:
                all_owners.add(owner)
            all_mints.add(mint)

        for mint in all_mints:
            decreases = []  # (owner, amount)
            increases = []
            done_keys = set()
            for key, prev_amt in pre_map.items():
                acct, m, owner = key
                if m != mint:
                    continue
                post_amt = post_map.get(key, 0)
                diff = round(post_amt - prev_amt, 6)
                if diff < -0.0001:
                    decreases.append((owner or acct, abs(diff)))
                    done_keys.add(key)
                elif diff > 0.0001:
                    increases.append((owner or acct, diff))
                    done_keys.add(key)
            # Check post-only entries (new ATAs)
            for key, post_amt in post_map.items():
                if key in done_keys:
                    continue
                acct, m, owner = key
                if m != mint:
                    continue
                if post_amt >= 0.0001:
                    increases.append((owner or acct, post_amt))

            # Pair decreases with increases
            for from_w, amt_out in decreases:
                remaining = amt_out
                for i, (to_w, amt_in) in enumerate(increases):
                    if remaining <= 0:
                        break
                    if to_w == from_w:
                        continue
                    take = min(remaining, amt_in)
                    if take >= 0.0001:
                        unit = f'TOKEN({mint[:4]})' if mint else 'TOKEN'
                        transfers.append({'from': from_w, 'to': to_w, 'amount': round(take, 6), 'unit': unit, 'time': time_str, 'sig': sig[:13]})
                        remaining = round(remaining - take, 6)
                        increases[i] = (to_w, round(amt_in - take, 6))

    return transfers

def trace_wallet(address, hops=1):
    """
    Trace fund flow from a wallet address.
    Returns dict with wallets and transfers.
    """
    wallets = {}
    transfers = []
    visited = set()
    queue = [(address, 0)]

    while queue:
        addr, depth = queue.pop(0)
        if addr in visited or depth > hops:
            continue
        visited.add(addr)

        if addr not in wallets:
            bal = get_balance(addr)
            wallets[addr] = {'address': addr, 'balance': bal.get('value', 0) / 1e9}

        sigs = get_signatures(addr, limit=20)
        for s_info in sigs:
            sig = s_info.get('signature', '')
            tx = get_transaction(sig)
            if not tx:
                continue

            tx_transfers = parse_transfers(tx, sig)
            for t in tx_transfers:
                if t['from'] and t['from'] not in wallets:
                    wallets[t['from']] = {'address': t['from']}
                if t['to'] and t['to'] not in wallets:
                    wallets[t['to']] = {'address': t['to']}

                transfers.append(t)

                # Queue next hops
                if depth < hops:
                    if t['from'] == addr and t['to']:
                        queue.append((t['to'], depth + 1))
                    elif t['to'] == addr and t['from']:
                        queue.append((t['from'], depth + 1))

            time.sleep(0.05)  # Rate limiting

    return {'wallets': list(wallets.values()), 'transfers': transfers}

def analyze_trace(wallets, transfers):
    """Post-process trace data to identify suspicious patterns."""
    # Find potential victim wallets (high outflows)
    sol_out = defaultdict(float)
    sol_in = defaultdict(float)
    for t in transfers:
        if t['unit'] == 'SOL':
            if t['from']: sol_out[t['from']] += t['amount']
            if t['to']: sol_in[t['to']] += t['amount']

    # Identify known patterns
    known = {}
    for addr, out in sol_out.items():
        if out > 0.01 and out > sol_in.get(addr, 0) * 2:
            known[addr] = {'label': 'POSIBLE VÍCTIMA', 'group': 'victim'}

    for addr, inn in sol_in.items():
        if inn > 0.01 and inn > sol_out.get(addr, 0) * 2:
            if addr not in known:
                known[addr] = {'label': 'POSIBLE RECOLECTOR', 'group': 'collector'}

    return known
