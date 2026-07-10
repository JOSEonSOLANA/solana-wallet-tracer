import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask, request, jsonify, send_from_directory

from database import init_db, save_trace, get_trace, get_all_traces, delete_trace, save_known_wallet, get_known_wallets, delete_known_wallet
from tracer import trace_wallet, analyze_trace, get_signatures, get_transaction, parse_transfers, RateLimitError

rpc_executor = ThreadPoolExecutor(max_workers=10)

app = Flask(__name__, static_folder='.')
BASE_DIR = os.path.dirname(__file__)
DATA_DIR = os.path.join(BASE_DIR, 'data')

_trace_lock = threading.Lock()
_trace_progress = {}

@app.route('/')
def index():
    return send_from_directory(BASE_DIR, 'fund_graph.html')

@app.route('/api/trace', methods=['POST'])
def api_trace():
    data = request.get_json()
    address = data.get('address', '').strip()
    hops = int(data.get('hops', 1))
    label = data.get('label', '')

    if not address:
        return jsonify({'error': 'Dirección requerida'}), 400

    # Check if already cached
    cached = get_trace(address)
    if cached and cached.get('status') == 'completed':
        return jsonify({'status': 'cached', 'data': cached})

    # Start tracing in background
    def _do_trace():
        with _trace_lock:
            _trace_progress[address] = {'status': 'processing', 'progress': 0}
            save_trace(address, {}, 'processing', hops, label)

        try:
            result = trace_wallet(address, hops=hops)
            known = analyze_trace(result['wallets'], result['transfers'])
            result['known'] = known

            save_trace(address, result, 'completed', hops, label)
            _trace_progress[address] = {'status': 'completed'}
        except Exception as e:
            save_trace(address, {'error': str(e)}, 'failed', hops, label)
            _trace_progress[address] = {'status': 'failed', 'error': str(e)}

    thread = threading.Thread(target=_do_trace, daemon=True)
    thread.start()

    return jsonify({'status': 'processing', 'message': 'Trazando wallet...'})

@app.route('/api/trace/<address>', methods=['GET'])
def api_get_trace(address):
    cached = get_trace(address)
    if cached:
        return jsonify(cached)
    return jsonify({'status': 'not_found'}), 404

@app.route('/api/trace/<address>/progress', methods=['GET'])
def api_trace_progress(address):
    prog = _trace_progress.get(address, {'status': 'unknown'})
    return jsonify(prog)

@app.route('/api/trace/<address>', methods=['DELETE'])
def api_delete_trace(address):
    delete_trace(address)
    _trace_progress.pop(address, None)
    return jsonify({'status': 'deleted'})

@app.route('/api/history', methods=['GET'])
def api_history():
    traces = get_all_traces()
    # Return lightweight list (without full data)
    summary = []
    for t in traces:
        summary.append({
            'wallet_address': t['wallet_address'],
            'label': t['label'],
            'status': t['status'],
            'hops': t['hops'],
            'created_at': t['created_at'],
            'wallet_count': len(t['data'].get('wallets', [])) if t.get('data') else 0,
            'transfer_count': len(t['data'].get('transfers', [])) if t.get('data') else 0,
        })
    return jsonify(summary)

@app.route('/api/visualization/<address>', methods=['GET'])
def api_visualization(address):
    """Returns data in the format expected by fund_graph.html."""
    cached = get_trace(address)
    if not cached or cached['status'] != 'completed':
        return jsonify({'error': 'No data for this wallet'}), 404

    data = cached['data']
    known = data.get('known', {})

    wallets = []
    for w in data.get('wallets', []):
        addr = w['address']
        entry = {'address': addr}
        if addr in known:
            entry['label'] = known[addr]['label']
            entry['is_known'] = True
        wallets.append(entry)

    transfers = [t for t in data.get('transfers', []) if t.get('from') and t.get('to')]

    return jsonify({'wallets': wallets, 'transfers': transfers})

@app.route('/api/transactions/<address>', methods=['GET'])
def api_transactions(address):
    """Returns transactions split by drain: ~5 before (vulnerability) and all after (fund flow)."""
    sigs = get_signatures(address, limit=100)
    if not sigs:
        return jsonify({'txs': [], 'drain_sig': None})

    # Auto-detect drain: check newest transactions first for drain pattern
    drain_sig = None
    drain_idx = -1
    for i, s_info in enumerate(sigs[:20]):  # check 20 newest
        sig = s_info.get('signature', '')
        try:
            tx = get_transaction(sig)
        except RateLimitError:
            return jsonify({'txs': [], 'drain_sig': None, 'rate_limited': True, 'error': 'Límite de API key alcanzado. Añade otra key de Helius.'})
        if not tx:
            continue
        meta = tx.get('meta', {})
        pre = meta.get('preBalances', [])
        post = meta.get('postBalances', [])
        accts = tx.get('transaction', {}).get('message', {}).get('accountKeys', [])
        if address in accts:
            idx = accts.index(address)
            bal_before = pre[idx] / 1e9 if len(pre) > idx else 0
            bal_after = post[idx] / 1e9 if len(post) > idx else 0
            lost = bal_before - bal_after
            if bal_before > 0.005 and lost > 0 and (lost / bal_before) > 0.8:
                drain_sig = sig
                drain_idx = i
                break

    # Split transactions: before drain (~5) and after drain (all)
    results_before = []
    results_after = []

    if drain_sig and drain_idx >= 0:
        # sigs is newest-first, so drain_idx is the drain position
        # "before drain" = transactions older than drain (higher index)
        # "after drain" = transactions newer than drain (lower index)
        before_sigs = sigs[drain_idx+1:drain_idx+6]  # 5 tx before drain
        after_sigs = sigs[:drain_idx]  # all tx after drain (newest first)

        for s_info in before_sigs:
            sig = s_info.get('signature', '')
            ts = s_info.get('blockTime', 0)
            results_before.append({
                'sig': sig,
                'sig_short': sig[:13],
                'time': time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(ts)) if ts else '',
                'slot': s_info.get('slot', 0),
                'phase': 'before'
            })

        for s_info in after_sigs:
            sig = s_info.get('signature', '')
            ts = s_info.get('blockTime', 0)
            results_after.append({
                'sig': sig,
                'sig_short': sig[:13],
                'time': time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(ts)) if ts else '',
                'slot': s_info.get('slot', 0),
                'phase': 'after'
            })

        # Add drain tx itself
        drain_ts = sigs[drain_idx].get('blockTime', 0)
        drain_entry = {
            'sig': drain_sig,
            'sig_short': drain_sig[:13],
            'time': time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(drain_ts)) if drain_ts else '',
            'slot': sigs[drain_idx].get('slot', 0),
            'phase': 'drain'
        }

        results = results_after + [drain_entry] + results_before
    else:
        # No drain detected, return last 20
        for s_info in sigs[:20]:
            sig = s_info.get('signature', '')
            ts = s_info.get('blockTime', 0)
            results.append({
                'sig': sig,
                'sig_short': sig[:13],
                'time': time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(ts)) if ts else '',
                'slot': s_info.get('slot', 0),
                'phase': 'normal'
            })

    return jsonify({'txs': results, 'drain_sig': drain_sig})

def _fetch_txs_parallel(sigs):
    """Fetch multiple transactions in parallel."""
    tx_map = {}
    futures = {rpc_executor.submit(get_transaction, s): s for s in sigs}
    for f in as_completed(futures):
        sig = futures[f]
        try:
            tx = f.result()
            if tx:
                tx_map[sig] = tx
        except Exception:
            pass
    return tx_map

@app.route('/api/trace-from-tx', methods=['POST'])
def api_trace_from_tx():
    """Trace fund flow starting from a specific transaction."""
    data = request.get_json()
    sig = data.get('sig', '').strip()
    address = data.get('address', '').strip()
    depth = int(data.get('depth', 1))

    if not sig:
        return jsonify({'error': 'Signature required'}), 400

    try:
        tx = get_transaction(sig)
    except RateLimitError:
        return jsonify({'error': 'Límite de API key alcanzado. Añade otra key de Helius.', 'rate_limited': True}), 429
    if not tx:
        return jsonify({'error': 'Transaction not found'}), 404

    transfers = parse_transfers(tx, sig)
    wallets = {}
    for t in transfers:
        if t.get('from'): wallets[t['from']] = {'address': t['from']}
        if t.get('to'): wallets[t['to']] = {'address': t['to']}

    # Detect drain
    is_drain = False
    drain_info = {}
    if address:
        meta = tx.get('meta', {})
        accts = tx.get('transaction', {}).get('message', {}).get('accountKeys', [])
        idx_to_pub = {i: a.get('pubkey', a) if isinstance(a, dict) else a for i, a in enumerate(accts)}
        pre_bals = meta.get('preBalances', [])
        post_bals = meta.get('postBalances', [])
        for i, pub in idx_to_pub.items():
            if pub == address and i < len(pre_bals) and i < len(post_bals):
                pre = pre_bals[i] / 1e9
                post = post_bals[i] / 1e9
                if pre > 0 and post < pre * 0.2:
                    is_drain = True
                    drain_info = {'balance_before': round(pre, 6), 'balance_after': round(post, 6), 'lost': round(pre - post, 6)}
                break

    # Follow the flow: parallel multi-depth BFS
    if depth > 0:
        visited_sigs = {sig}
        wallet_addrs = set()
        for t in transfers:
            if t.get('from'): wallet_addrs.add(t['from'])
            if t.get('to'): wallet_addrs.add(t['to'])

        main_recipients = set()
        for t in transfers:
            if t.get('from') == address and t.get('to'):
                main_recipients.add(t['to'])

        # Level 1: scan all wallets from initial transaction
        pending_sigs = []
        for addr in wallet_addrs:
            try:
                sig_limit = 25 if addr in main_recipients else 10
                sigs = get_signatures(addr, limit=sig_limit)
                for ns in sigs:
                    nsig = ns.get('signature', '')
                    if nsig not in visited_sigs:
                        pending_sigs.append(nsig)
                        visited_sigs.add(nsig)
            except RateLimitError:
                raise
            except Exception:
                pass

        tx_map = _fetch_txs_parallel(pending_sigs) if pending_sigs else {}
        for nsig, ntx in tx_map.items():
            ntransfers = parse_transfers(ntx, nsig)
            for nt in ntransfers:
                if nt.get('from'): wallets[nt['from']] = {'address': nt['from']}
                if nt.get('to'): wallets[nt['to']] = {'address': nt['to']}
                transfers.append(nt)

        # Level 2: scan newly discovered wallets (depth >= 2)
        if depth >= 2:
            new_wallet_addrs = set()
            for t in transfers:
                if t.get('from'): new_wallet_addrs.add(t['from'])
                if t.get('to'): new_wallet_addrs.add(t['to'])
            new_wallet_addrs -= wallet_addrs

            if new_wallet_addrs:
                level2_sigs = []
                for addr in new_wallet_addrs:
                    try:
                        sigs = get_signatures(addr, limit=5)
                        for ns in sigs:
                            nsig = ns.get('signature', '')
                            if nsig not in visited_sigs:
                                level2_sigs.append(nsig)
                                visited_sigs.add(nsig)
                    except RateLimitError:
                        raise
                    except Exception:
                        pass

                tx_map2 = _fetch_txs_parallel(level2_sigs) if level2_sigs else {}
                for nsig, ntx in tx_map2.items():
                    ntransfers = parse_transfers(ntx, nsig)
                    for nt in ntransfers:
                        if nt.get('from'): wallets[nt['from']] = {'address': nt['from']}
                        if nt.get('to'): wallets[nt['to']] = {'address': nt['to']}
                        transfers.append(nt)

    # Find vulnerability tx among 5 before-drain transactions
    vulnerability_info = None
    if is_drain and address:
        try:
            # Use before_sigs from request if available, else re-fetch
            before_sigs = data.get('before_sigs', None)
            if not before_sigs:
                sigs = get_signatures(address, limit=50)
                drain_idx = -1
                for i, s_info in enumerate(sigs):
                    if s_info.get('signature') == sig:
                        drain_idx = i
                        break
                if drain_idx >= 0:
                    before_sigs = [s.get('signature', '') for s in sigs[drain_idx+1:drain_idx+6]]

            if before_sigs:
                COMMON_PROGRAMS = [
                    '11111111111111111111111111111111',
                    'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA',
                    'ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL',
                    'ComputeBudget111111111111111111111111111111',
                    'Vote111111111111111111111111111111111111111',
                    'Stake11111111111111111111111111111111111111',
                    'KeccakSecp256k11111111111111111111111111111',
                    'So1endDv2NyzpJequhbzEf2NfCCfFST5qC2ExCrcQmD',
                ]
                for s_info in before_sigs:
                    b_sig = s_info if isinstance(s_info, str) else s_info.get('signature', '')
                    b_tx = get_transaction(b_sig)
                    if not b_tx:
                        continue
                    msg = b_tx.get('transaction', {}).get('message', {})
                    accts = msg.get('accountKeys', [])
                    is_suspicious = False
                    for instr in msg.get('instructions', []):
                        prog_idx = instr.get('programIdIndex', -1)
                        if 0 <= prog_idx < len(accts):
                            prog = accts[prog_idx]
                            if isinstance(prog, dict):
                                prog = prog.get('pubkey', '')
                            if prog not in COMMON_PROGRAMS:
                                is_suspicious = True
                                malicious_prog = prog
                                inst_data = instr.get('data', '')[:32]
                                break
                    if is_suspicious:
                        b_time = b_tx.get('blockTime', 0)
                        b_time_str = time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(b_time)) if b_time else ''
                        # Extract fee payer and dApp info
                        fee_payer = ''
                        dapp_hint = ''
                        if len(accts) > 0:
                            first = accts[0]
                            fee_payer = first.get('pubkey', first) if isinstance(first, dict) else first
                        # Check for known phishing patterns in account keys
                        acct_strs = [str(a) for a in accts[:8]]
                        vulnerability_info = {
                            'sig': b_sig,
                            'sig_short': b_sig[:13],
                            'time': b_time_str,
                            'blockTime': b_time,
                            'program_id': malicious_prog,
                            'fee_payer': fee_payer,
                            'instr_count': len(msg.get('instructions', [])),
                            'instr_data': inst_data,
                        }
                        break
        except RateLimitError:
            raise
        except Exception:
            pass

    # Apply cutoff: filter out transfers that happened before the vulnerability tx
    if vulnerability_info and vulnerability_info.get('time'):
        cutoff = vulnerability_info['time']
        transfers = [t for t in transfers if t.get('time', '') >= cutoff]

    known = analyze_trace(list(wallets.values()), transfers)
    paired_transfers = [t for t in transfers if t.get('from') and t.get('to')]
    paired_transfers.sort(key=lambda x: x.get('time', ''))
    return jsonify({
        'wallets': list(wallets.values()),
        'transfers': paired_transfers,
        'known': known,
        'is_drain': is_drain,
        'drain_info': drain_info,
        'vulnerability': vulnerability_info,
        'source_tx': paired_transfers[0] if paired_transfers else None,
    })
def api_known_wallets():
    return jsonify(get_known_wallets())

@app.route('/api/known-wallets', methods=['POST'])
def api_add_known_wallet():
    data = request.get_json()
    save_known_wallet(data['address'], data.get('label', ''), data.get('group_name', 'intermediary'), data.get('color', ''))
    return jsonify({'status': 'saved'})

@app.route('/api/known-wallets/<address>', methods=['DELETE'])
def api_delete_known_wallet(address):
    delete_known_wallet(address)
    return jsonify({'status': 'deleted'})

# Helius API key management
CONFIG_FILE = os.path.join(DATA_DIR, 'config.json')

def _load_config():
    try:
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    except:
        return {}

def _save_config(cfg):
    with open(CONFIG_FILE, 'w') as f:
        json.dump(cfg, f)

@app.route('/api/helius-key', methods=['GET'])
def api_get_helius_key():
    cfg = _load_config()
    key = cfg.get('helius_key', '')
    # Show masked preview; include unmasked key so frontend can pre-fill
    preview = key[:8] + '...' if len(key) > 8 else (key or '')
    return jsonify({'key': key, 'preview': preview})

@app.route('/api/helius-key', methods=['POST'])
def api_set_helius_key():
    data = request.get_json()
    key = data.get('key', '').strip()
    cfg = _load_config()
    cfg['helius_key'] = key
    _save_config(cfg)
    # Update environment variable for current process
    os.environ['HELIUS_API_KEY'] = key
    return jsonify({'status': 'saved'})

@app.route('/api/debug-rpc')
def api_debug_rpc():
    """Debug endpoint to test RPC connectivity."""
    import tracer as t
    sig = '5NyCxwh5uNj7sVufVpQZ4ZdNzj4ZUKs4P85QHMSBS2xXR3axhxsYCPJCUxXBZeA7vQntHLZLEJ1XG59G6c6EHqGx'
    results = {}
    # Test each RPC endpoint individually
    for ep in t._get_rpc_endpoints():
        try:
            t._rpc_call('getTransaction', [sig, {'encoding': 'json', 'maxSupportedTransactionVersion': 0}], endpoint=ep)
            results[ep[:40]] = 'OK'
        except Exception as e:
            results[ep[:40]] = str(e)[:100]
    key = os.environ.get('HELIUS_API_KEY', '')
    return jsonify({'has_key': bool(key), 'key_preview': key[:8]+'..' if key else 'none', 'endpoints': results})

DEFAULT_HELIUS_KEY = 'a6bb8dd1-5315-4b04-8223-b0dff0badb13'

if __name__ == '__main__':
    init_db()
    os.makedirs(DATA_DIR, exist_ok=True)
    # Load Helius key from config (or use default)
    cfg = _load_config()
    key = cfg.get('helius_key', '') or DEFAULT_HELIUS_KEY
    if key:
        os.environ['HELIUS_API_KEY'] = key
    # Save default key to config if not set
    if not cfg.get('helius_key'):
        cfg['helius_key'] = DEFAULT_HELIUS_KEY
        _save_config(cfg)
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_ENV') == 'development'
    host = os.environ.get('HOST', '0.0.0.0')
    print(f"Servidor iniciado en http://{host}:{port}")
    print(f"Directorio de datos: {DATA_DIR}")
    app.run(host=host, port=port, debug=debug)
