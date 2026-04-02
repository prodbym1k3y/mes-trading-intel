"""
AMP Futures / Rithmic live account connector.

Uses the async_rithmic library to pull trade history from Rithmic's ORDER_PLANT.
"""
import asyncio
from datetime import datetime, date, timedelta
from amp_import import calculate_pnl, get_tick_value, normalize_symbol, pair_executions

# Rithmic server URLs — format: "hostname:port" (library adds wss:// automatically)
# Source: async_rithmic docs + Rithmic conformance docs
# NOTE: Live (Rithmic 01) requires passing Rithmic conformance test for your app.
#       If you get timeouts on live, use "Rithmic Test" or "Rithmic Paper Trading" first.
RITHMIC_SERVERS = {
    # Test environment (open access, use for development)
    'Rithmic Test':           'rituz00100.rithmic.com:443',

    # Paper trading
    'Rithmic Paper Trading':  'ritpa11120.11.rithmic.com:443',

    # Live trading (requires conformance approval)
    'Rithmic 01':             'ritpz01000.01.rithmic.com:443',

    # Prop firms (use paper trading infra)
    'TopstepTrader':          'ritpa11120.11.rithmic.com:443',
    'MES Capital':            'ritpz01000.01.rithmic.com:443',
}

_connection_status = {'connected': False, 'last_sync': None, 'account': None, 'error': None}


def get_status():
    return dict(_connection_status)


def _get_url(system):
    """Resolve the server URL for a given system name."""
    system = system or 'Rithmic Paper Trading'

    if system in RITHMIC_SERVERS:
        return RITHMIC_SERVERS[system]

    # Default to test server (most accessible)
    return 'rituz00100.rithmic.com:443'


async def _connect_client(credentials):
    """Create and connect a RithmicClient with the right URL."""
    from async_rithmic import RithmicClient

    system = credentials.get('system', 'Rithmic Paper Trading')
    url = _get_url(system)

    client = RithmicClient(
        user=credentials['user'],
        password=credentials['password'],
        system_name=system,
        app_name='TradeJournal',
        app_version='1.0',
        url=url,
    )

    # Fix SSL hostname verification issue:
    # Rithmic's server cert is *.rithmic.com (single-level wildcard)
    # but their actual hostnames are 2+ levels deep (e.g. ritpa11120.11.rithmic.com)
    # This causes SSL CERTIFICATE_VERIFY_FAILED on hostname mismatch.
    # We still verify the cert itself, just not the hostname.
    if hasattr(client, 'ssl_context') and client.ssl_context:
        client.ssl_context.check_hostname = False

    return client


async def _fetch_fills(credentials, target_date):
    """Fetch fills from Rithmic for a given date."""
    from async_rithmic import SysInfraType

    client = await _connect_client(credentials)

    # Use timeout to avoid hanging forever on unreachable servers
    try:
        await asyncio.wait_for(
            client.connect(plants=[SysInfraType.ORDER_PLANT]),
            timeout=15
        )
    except asyncio.TimeoutError:
        raise RuntimeError(
            "Connection timed out. If using 'Rithmic 01' (live), your app may need "
            "Rithmic conformance approval. Try 'Rithmic Paper Trading' or 'Rithmic Test' instead."
        )

    _connection_status['connected'] = True
    _connection_status['error'] = None

    try:
        accounts = await client.list_accounts()
        if not accounts:
            raise RuntimeError("No accounts found on this Rithmic login")

        account_id = credentials.get('account_id') or (
            getattr(accounts[0], 'account_id', None) or str(accounts[0])
        )
        _connection_status['account'] = account_id

        date_str = target_date.strftime('%Y%m%d')
        orders = await client.show_order_history_summary(date=date_str)

        executions = []
        for order in orders:
            fill_price = getattr(order, 'avg_fill_price', None) or getattr(order, 'price', None)
            fill_qty = getattr(order, 'total_fill_qty', None) or getattr(order, 'qty', None) or getattr(order, 'quantity', 0)

            if not fill_price or fill_qty == 0:
                continue

            buy_sell = getattr(order, 'buy_sell_type', None) or getattr(order, 'transaction_type', None)
            if buy_sell == 1 or str(buy_sell).upper() in ('BUY', 'B'):
                side = 'BUY'
            elif buy_sell == 2 or str(buy_sell).upper() in ('SELL', 'S'):
                side = 'SELL'
            else:
                continue

            fill_time = datetime.combine(target_date, datetime.min.time())
            for time_attr in ('update_time', 'fill_time', 'time', 'ssboe'):
                t = getattr(order, time_attr, None)
                if t:
                    try:
                        if isinstance(t, (int, float)):
                            fill_time = datetime.fromtimestamp(t)
                        else:
                            fill_time = datetime.strptime(
                                f"{target_date.isoformat()} {t}", "%Y-%m-%d %H:%M:%S"
                            )
                    except (ValueError, TypeError, OSError):
                        continue
                    break

            symbol = getattr(order, 'ticker', None) or getattr(order, 'symbol', 'ES')
            commission = getattr(order, 'commission', 0) or 0

            executions.append({
                'symbol': str(symbol),
                'side': side,
                'quantity': int(fill_qty),
                'price': float(fill_price),
                'datetime': fill_time,
                'fee': abs(float(commission)),
            })

        trades = pair_executions(executions)
        for t in trades:
            t['source'] = 'rithmic'

        _connection_status['last_sync'] = datetime.now().isoformat()
        return trades

    finally:
        await client.disconnect()


def fetch_fills_sync(credentials, target_date):
    """Synchronous wrapper for fetching fills."""
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(_fetch_fills(credentials, target_date))
    except Exception as e:
        _connection_status['connected'] = False
        _connection_status['error'] = str(e)
        raise
    finally:
        loop.close()


def test_connection(credentials):
    """Test if credentials work. Returns account list on success."""
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def _test():
            from async_rithmic import SysInfraType

            client = await _connect_client(credentials)

            try:
                await asyncio.wait_for(
                    client.connect(plants=[SysInfraType.ORDER_PLANT]),
                    timeout=15
                )
            except asyncio.TimeoutError:
                system = credentials.get('system', 'Rithmic Paper Trading')
                raise RuntimeError(
                    f"Connection to '{system}' timed out. "
                    f"If using 'Rithmic 01' (live), your app may need Rithmic conformance approval. "
                    f"Try 'Rithmic Paper Trading' or 'Rithmic Test' instead."
                )

            try:
                accounts = await client.list_accounts()
                result = []
                for a in accounts:
                    acc_id = getattr(a, 'account_id', None) or str(a)
                    fcm = getattr(a, 'fcm_id', '')
                    result.append({'account_id': acc_id, 'fcm_id': fcm})
                return result
            finally:
                await client.disconnect()

        result = loop.run_until_complete(_test())
        _connection_status['connected'] = True
        _connection_status['error'] = None
        return {'ok': True, 'accounts': result}
    except Exception as e:
        _connection_status['connected'] = False
        _connection_status['error'] = str(e)
        return {'ok': False, 'error': str(e)}
    finally:
        loop.close()
