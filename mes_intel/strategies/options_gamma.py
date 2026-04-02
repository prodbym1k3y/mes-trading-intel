"""Options Gamma Exposure (GEX) Strategy.

Uses live options GEX data: gamma flip, dealer positioning, call/put walls,
max pain, and put/call ratio to generate directional signals for MES.
"""
from __future__ import annotations

import time
import numpy as np
from .base import Strategy, StrategyResult

# Data staleness threshold (seconds)
_MAX_GEX_AGE = 1800   # 30 minutes


class OptionsGammaStrategy(Strategy):
    """Live options GEX: gamma flip, dealer positioning, 0DTE walls."""

    name = "options_gamma"
    description = "Live options GEX: gamma flip, dealer positioning, 0DTE walls"

    def required_data(self) -> list[str]:
        return ['prices', 'options_data']

    def evaluate(self, market_data: dict) -> StrategyResult:
        prices = market_data.get('prices', [])
        options = market_data.get('options_data', {})

        if not prices:
            return StrategyResult(
                name=self.name, score=0.0, confidence=0.0, direction='FLAT',
                meta={'reason': 'no price data'},
            )

        # MES current price; SPY ≈ MES / 10
        mes_price = float(prices[-1])
        spy_price = mes_price / 10.0

        if not options:
            return StrategyResult(
                name=self.name, score=0.0, confidence=0.0, direction='FLAT',
                meta={'reason': 'no options_data in market_data'},
            )

        # ----------------------------------------------------------------
        # Extract GEX levels
        # ----------------------------------------------------------------
        flip_price: float | None = options.get('flip_price')
        call_wall: float | None = options.get('call_wall')
        put_wall: float | None = options.get('put_wall')
        max_pain: float | None = options.get('max_pain')
        net_gex: float = float(options.get('net_gex', 0))
        pcr: float = float(options.get('put_call_ratio', 1.0))
        dealer_pos: str = options.get('dealer_positioning', '')
        fetched_at: float = options.get('_fetched_at', 0.0)

        # ----------------------------------------------------------------
        # Data freshness
        # ----------------------------------------------------------------
        if fetched_at > 0:
            data_age = time.time() - fetched_at
        else:
            data_age = options.get('age_sec', _MAX_GEX_AGE)

        if data_age > _MAX_GEX_AGE:
            age_factor = 0.2
        elif data_age > 600:
            age_factor = max(0.5, 1.0 - (data_age - 600) / (_MAX_GEX_AGE - 600) * 0.5)
        else:
            age_factor = 1.0

        # ----------------------------------------------------------------
        # Active signals list: (name, score, weight)
        # ----------------------------------------------------------------
        signal_list: list[tuple[str, float, float]] = []
        active_signals: list[str] = []

        # ----------------------------------------------------------------
        # 1. Gamma flip (above/below flip price)
        # ----------------------------------------------------------------
        gamma_regime = 'unknown'
        if flip_price is not None and flip_price > 0:
            flip_in_spy = flip_price  # assume GEX levels are in SPY terms
            if spy_price > flip_in_spy:
                # Positive gamma zone — dealers are long gamma
                # Market is mean-reverting; fade extremes (mild bearish signal when extended up)
                dist_pct = (spy_price - flip_in_spy) / flip_in_spy * 100
                if dist_pct > 2.0:
                    sig = -0.2  # well above flip, dealers sell rips
                    active_signals.append(f"above_flip +{dist_pct:.1f}% (long_gamma)")
                else:
                    sig = 0.0
                    active_signals.append(f"above_flip (long_gamma, near)")
                gamma_regime = 'long_gamma'
            else:
                # Negative gamma zone — dealers are short gamma
                # Moves get amplified; follow momentum
                dist_pct = (flip_in_spy - spy_price) / flip_in_spy * 100
                if dist_pct > 2.0:
                    sig = -0.3  # well below flip, gamma crash risk
                    active_signals.append(f"below_flip -{dist_pct:.1f}% (short_gamma, trend)")
                else:
                    sig = -0.1
                    active_signals.append(f"below_flip (short_gamma, near)")
                gamma_regime = 'short_gamma'

            signal_list.append(('gamma_flip', sig, 0.25))

        # ----------------------------------------------------------------
        # 2. Proximity to call/put walls (strongest signal)
        # ----------------------------------------------------------------
        wall_pct_threshold = 0.003  # 0.3%

        if call_wall is not None and call_wall > 0:
            dist_to_call = (call_wall - spy_price) / spy_price
            if -0.01 < dist_to_call <= 0:
                # Broke through call wall — gamma squeeze, very bullish
                signal_list.append(('call_wall_breakout', 0.9, 0.30))
                active_signals.append(f"CALL WALL BREAKOUT @ {call_wall:.2f} SPY")
            elif 0 < dist_to_call <= wall_pct_threshold:
                # Approaching call wall from below — bearish (hard resistance)
                signal_list.append(('near_call_wall', -0.6, 0.30))
                active_signals.append(f"near_call_wall {call_wall:.2f} ({dist_to_call*100:.2f}%)")
            elif dist_to_call > wall_pct_threshold:
                # Call wall is a distant magnet — slightly bullish pull
                if dist_to_call < 0.01:
                    signal_list.append(('call_wall_magnet', 0.15, 0.10))

        if put_wall is not None and put_wall > 0:
            dist_from_put = (spy_price - put_wall) / spy_price
            if -0.01 < dist_from_put <= 0:
                # Broke through put wall — gamma crash, very bearish
                signal_list.append(('put_wall_breakdown', -0.9, 0.30))
                active_signals.append(f"PUT WALL BREAKDOWN @ {put_wall:.2f} SPY")
            elif 0 < dist_from_put <= wall_pct_threshold:
                # Approaching put wall from above — bullish (hard support)
                signal_list.append(('near_put_wall', 0.6, 0.30))
                active_signals.append(f"near_put_wall {put_wall:.2f} ({dist_from_put*100:.2f}%)")
            elif dist_from_put > wall_pct_threshold:
                # Put wall is a distant magnet — slightly bearish pull (gravity down)
                if dist_from_put < 0.01:
                    signal_list.append(('put_wall_magnet', -0.1, 0.10))

        # ----------------------------------------------------------------
        # 3. Net GEX magnitude
        # ----------------------------------------------------------------
        gex_regime = 'neutral'
        if net_gex > 2_000_000_000:
            gex_regime = 'strong_positive'
            # Extremely mean-reverting — strongly fade any directional move
            signal_list.append(('net_gex_high', -0.2, 0.15))  # lean against trend
            active_signals.append(f"net_GEX +${net_gex/1e9:.1f}B (strong pinning)")
        elif net_gex > 500_000_000:
            gex_regime = 'positive'
            signal_list.append(('net_gex_moderate', -0.1, 0.10))
            active_signals.append(f"net_GEX +${net_gex/1e9:.1f}B (pinning)")
        elif net_gex < -1_000_000_000:
            gex_regime = 'negative'
            # Negative gamma — moves get amplified; signal is directional amplifier,
            # not standalone signal. Reduce weight unless combined with other signals.
            signal_list.append(('net_gex_negative', 0.0, 0.10))  # informational
            active_signals.append(f"net_GEX -${abs(net_gex)/1e9:.1f}B (amplified moves)")
        elif net_gex < 0:
            gex_regime = 'slight_negative'

        # ----------------------------------------------------------------
        # 4. Put/Call ratio (contrarian)
        # ----------------------------------------------------------------
        pcr_signal = 0.0
        if pcr < 0.7:
            # Too bullish / complacent → mild bearish contrarian
            pcr_signal = -0.3
            active_signals.append(f"PCR={pcr:.2f} (complacent, contrarian bearish)")
        elif pcr > 1.3:
            # Too fearful → mild bullish contrarian
            pcr_signal = 0.3
            active_signals.append(f"PCR={pcr:.2f} (fearful, contrarian bullish)")
        elif pcr < 0.85:
            pcr_signal = -0.1
        elif pcr > 1.1:
            pcr_signal = 0.1

        if abs(pcr_signal) > 0:
            signal_list.append(('put_call_ratio', pcr_signal, 0.15))

        # ----------------------------------------------------------------
        # 5. Max pain magnet (same-day expiry pull)
        # ----------------------------------------------------------------
        # Detect 0DTE (we apply this only if max_pain is defined)
        if max_pain is not None and max_pain > 0:
            pain_dist = spy_price - max_pain
            if pain_dist > 3.0:
                # Price well above max pain — gravity pulls it down
                signal_list.append(('max_pain_pulldown', -0.2, 0.10))
                active_signals.append(f"max_pain={max_pain:.1f} SPY, above by {pain_dist:.1f}pts")
            elif pain_dist < -3.0:
                # Price well below max pain — gravity pulls it up
                signal_list.append(('max_pain_pullup', 0.2, 0.10))
                active_signals.append(f"max_pain={max_pain:.1f} SPY, below by {abs(pain_dist):.1f}pts")

        # ----------------------------------------------------------------
        # Score aggregation
        # ----------------------------------------------------------------
        if not signal_list:
            return StrategyResult(
                name=self.name, score=0.0, confidence=0.0, direction='FLAT',
                meta={
                    'reason': 'no actionable GEX signals',
                    'flip_price': flip_price,
                    'call_wall': call_wall,
                    'put_wall': put_wall,
                    'net_gex': net_gex,
                    'put_call_ratio': pcr,
                    'max_pain': max_pain,
                    'gex_regime': gex_regime,
                    'gamma_regime': gamma_regime,
                    'dealer_positioning': dealer_pos,
                    'active_signals': active_signals,
                    'data_age_sec': data_age,
                },
            )

        total_weight = sum(w for _, _, w in signal_list)
        if total_weight > 0:
            weighted_score = sum(s * w for _, s, w in signal_list) / total_weight
        else:
            weighted_score = 0.0

        score = float(np.clip(weighted_score, -1.0, 1.0))

        # ----------------------------------------------------------------
        # Confidence: agreement + magnitude + freshness
        # ----------------------------------------------------------------
        nonzero_sigs = [s for _, s, _ in signal_list if abs(s) > 0.05]
        if nonzero_sigs:
            pos = sum(1 for s in nonzero_sigs if s > 0)
            neg = sum(1 for s in nonzero_sigs if s < 0)
            agreement = max(pos, neg) / len(nonzero_sigs)
        else:
            agreement = 0.3

        confidence = float(np.clip(
            (agreement * 0.6 + min(abs(score), 1.0) * 0.4) * age_factor,
            0.0,
            1.0,
        ))

        # ----------------------------------------------------------------
        # Direction
        # ----------------------------------------------------------------
        if score > 0.15:
            direction = 'LONG'
        elif score < -0.15:
            direction = 'SHORT'
        else:
            direction = 'FLAT'

        # Format net GEX for display
        if abs(net_gex) >= 1_000_000_000:
            gex_display = f"{'+' if net_gex >= 0 else ''}${net_gex/1e9:.2f}B"
        elif abs(net_gex) >= 1_000_000:
            gex_display = f"{'+' if net_gex >= 0 else ''}${net_gex/1e6:.0f}M"
        else:
            gex_display = f"{net_gex:+.0f}"

        meta = {
            'flip_price': flip_price,
            'call_wall': call_wall,
            'put_wall': put_wall,
            'net_gex': net_gex,
            'net_gex_display': gex_display,
            'put_call_ratio': pcr,
            'max_pain': max_pain,
            'gex_regime': gex_regime,
            'gamma_regime': gamma_regime,
            'dealer_positioning': dealer_pos,
            'active_signals': active_signals,
            'signal_breakdown': [
                {'name': n, 'score': round(s, 3), 'weight': round(w, 3)}
                for n, s, w in signal_list
            ],
            'data_age_sec': round(data_age),
            'age_factor': round(age_factor, 3),
            'spy_price_used': round(spy_price, 2),
            'mes_price': round(mes_price, 2),
        }

        return StrategyResult(
            name=self.name,
            score=score,
            confidence=confidence,
            direction=direction,
            meta=meta,
        )
