"""Regression tests verifying all audit blockers are fixed."""

import pytest
import os
import sys
import tempfile
import shutil
from datetime import datetime, timezone

from core.models import Candle, SwingPoint, RiskAssessment, DataSafetyStatus, DataHealthResult, TradePlan
from config.constants import DataSafetyStatus, DerivativesStatus, DataSource, TradeDirection, SetupType, TriggerState, SourceHealthStatus
from engines.structure_engine import MarketStructureEngine
from engines.data_health import DataHealthEngine
from engines.derivatives_engine import DerivativesEngine, DerivativesField
from engines.trade_plan_engine import TradePlanEngine
from config.settings import get_settings, BotSettings
from core.state import BotState
from data.database import DatabaseManager


class TestLookaheadSafety:
    """Verify zero-lookahead guarantees."""

    def test_swing_point_has_dual_timestamps(self):
        """SwingPoint MUST have swing_time and confirmed_at set."""
        engine = MarketStructureEngine(left_bars=2, right_bars=2)
        candles = []
        highs = [100, 102, 105, 108, 110, 120, 112, 107, 104, 101]
        for i, h in enumerate(highs):
            candles.append(
                Candle(
                    timestamp=1000 + i * 3600,
                    open=h - 5, high=h, low=h - 8, close=h - 2,
                    volume=100.0, is_closed=True,
                )
            )

        sh_confirmed, _ = engine.find_confirmed_swings(candles[:8])
        swing = next((s for s in sh_confirmed if s.candle_index == 5), None)
        assert swing is not None, "Swing point at index 5 should be confirmed"
        assert swing.swing_time == 19000, f"swing_time should be 19000 (timestamp of candle at index 5), got {swing.swing_time}"
        assert swing.confirmed_at > swing.swing_time, "confirmed_at must be > swing_time"
        assert swing.confirmed_at > 0, "confirmed_at must be set"

    def test_confirmed_at_after_swing_time(self):
        """confirmed_at must always be after swing_time."""
        engine = MarketStructureEngine(left_bars=2, right_bars=2)
        candles = []
        for i in range(10):
            candles.append(
                Candle(
                    timestamp=1000 + i * 3600,
                    open=100.0, high=100.0, low=90.0, close=95.0,
                    volume=100.0, is_closed=True,
                )
            )
        # Add a peak at index 5
        candles[5] = Candle(
            timestamp=1000 + 5 * 3600,
            open=120.0, high=125.0, low=115.0, close=122.0,
            volume=100.0, is_closed=True,
        )

        sh, _ = engine.find_confirmed_swings(candles)
        for s in sh:
            assert s.confirmed_at >= s.swing_time + 3600, \
                f"confirmed_at ({s.confirmed_at}) must be after swing_time ({s.swing_time})"

    def test_candle_is_closed_no_default(self):
        """Candle.is_closed must be explicitly set — no latent default."""
        with pytest.raises(Exception):
            Candle(
                timestamp=1000, open=100.0, high=105.0, low=95.0, close=102.0, volume=10.0
            )

    def test_backtest_only_uses_closed_candles(self):
        """Backtest must filter to only closed candles."""
        from backtest.data_loader import HistoricalDataLoader
        loader = HistoricalDataLoader()
        dataset = loader.generate_synthetic_dataset(num_5m_bars=100, seed=42)

        for tf, candles in dataset.items():
            for c in candles:
                assert c.is_closed is True, f"{tf} candle at {c.timestamp} not closed"


class TestDerivativesIntegrity:
    """Verify derivatives data source handling."""

    def test_unavailable_sources_return_unavailable(self):
        """When all derivatives sources are unavailable, status must be UNAVAILABLE."""
        engine = DerivativesEngine()
        result = engine.evaluate_derivatives(
            candidate_direction=TradeDirection.LONG,
            setup_type=SetupType.TREND_PULLBACK,
            price_change_pct=0.0,
            oi_field=DerivativesField(value=None, source=DataSource.UNAVAILABLE),
            oi_change_field=DerivativesField(value=None, source=DataSource.UNAVAILABLE),
            funding_field=DerivativesField(value=None, source=DataSource.UNAVAILABLE),
            ls_field=DerivativesField(value=None, source=DataSource.UNAVAILABLE),
            taker_field=DerivativesField(value=None, source=DataSource.UNAVAILABLE),
            liquidation_field=DerivativesField(value=None, source=DataSource.UNAVAILABLE),
        )
        assert result.status == DerivativesStatus.UNAVAILABLE

    def test_coinglass_unavailable_not_neutral(self):
        """CoinGlass unavailable must NOT return NEUTRAL with fake data."""
        engine = DerivativesEngine()
        from config.constants import TradeDirection, SetupType
        result = engine.evaluate_derivatives(
            candidate_direction=TradeDirection.LONG,
            setup_type=SetupType.TREND_PULLBACK,
            price_change_pct=0.0,
            oi_field=DerivativesField(value=None, source=DataSource.UNAVAILABLE),
            oi_change_field=DerivativesField(value=None, source=DataSource.UNAVAILABLE),
            funding_field=DerivativesField(value=None, source=DataSource.UNAVAILABLE),
            ls_field=DerivativesField(value=None, source=DataSource.UNAVAILABLE),
            taker_field=DerivativesField(value=None, source=DataSource.UNAVAILABLE),
            liquidation_field=DerivativesField(value=None, source=DataSource.UNAVAILABLE),
        )
        assert result.status != DerivativesStatus.NEUTRAL, "Unavailable sources must not produce NEUTRAL"
        assert result.status == DerivativesStatus.UNAVAILABLE

    def test_derivatives_field_provenance(self):
        """Each derivatives field must carry source metadata."""
        field = DerivativesField(value=50000.0, source=DataSource.BINANCE)
        assert field.value == 50000.0
        assert field.source == DataSource.BINANCE
        assert field.source != DataSource.UNAVAILABLE


class TestConfigIntegrity:
    """Verify configuration values are properly sourced."""

    def test_settings_uses_hypotheses_for_fees(self):
        """Fees must come from hypotheses, not hardcoded."""
        settings = get_settings()
        assert settings.TAKER_FEE_PCT == 0.0004
        assert settings.SLIPPAGE_PCT == 0.0002
        from config.hypotheses import INITIAL_HYPOTHESES
        assert settings.TAKER_FEE_PCT == INITIAL_HYPOTHESES["taker_fee_pct"]

    def test_settings_uses_hypotheses_for_thresholds(self):
        """Strategy thresholds must come from hypotheses."""
        settings = get_settings()
        from config.hypotheses import INITIAL_HYPOTHESES
        assert settings.WICK_REJECTION_RATIO == INITIAL_HYPOTHESES["wick_rejection_ratio"]
        assert settings.MIN_RISK_REWARD == INITIAL_HYPOTHESES["min_risk_reward_ratio"]
        assert settings.SR_CLUSTERING_TOLERANCE_PCT == INITIAL_HYPOTHESES["sr_clustering_tolerance_pct"]


class TestDatabasePersistence:
    """Verify database layer works."""

    @pytest.fixture(autouse=True)
    def setup_db(self, tmp_path):
        """Create a temporary database for testing."""
        self.db_path = str(tmp_path / "test_bot.db")
        self.db_url = f"sqlite:///{self.db_path}"
        self.db = DatabaseManager(self.db_url)
        yield
        self.db.close()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_database_tables_created(self, tmp_path):
        """All tables must be created by migration."""
        import sqlite3
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}
        conn.close()
        assert "trades" in tables
        assert "decisions" in tables
        assert "bot_state" in tables

    def test_save_and_load_trade(self, tmp_path):
        """Trade records can be persisted and loaded."""
        from core.models import TradeRecord, SetupType, TradeDirection

        trade = TradeRecord(
            trade_id="TEST-001",
            symbol="BTC/USDT",
            setup_type=SetupType.TREND_PULLBACK,
            direction=TradeDirection.LONG,
            entry_time=1700000000000,
            entry_price=65000.0,
            stop_loss=64500.0,
            tp1=65800.0,
            tp2=66500.0,
            size_btc=0.1,
            size_usdt=6500.0,
            pnl_usdt=100.0,
            pnl_pct=1.5,
            r_multiple=2.0,
            mfe=0.02,
            mae=0.005,
            is_closed=True,
            fees_paid_usdt=5.20,
        )
        self.db.save_trade(trade)

        loaded = self.db.load_trades()
        assert len(loaded) == 1
        assert loaded[0].trade_id == "TEST-001"
        assert loaded[0].pnl_usdt == 100.0

    def test_save_and_load_state_snapshot(self, tmp_path):
        """BotState can be saved and restored from database."""
        import json
        state = BotState(account_balance_usdt=10_500.0, consecutive_losses=2)
        snapshot = state.to_dict()
        # Ensure recent_regimes_4h is JSON-serialized for database
        snapshot["recent_regimes_4h"] = json.dumps(snapshot["recent_regimes_4h"])
        self.db.save_state_snapshot(snapshot)

        row = self.db.load_latest_state()
        assert row is not None
        assert row["account_balance_usdt"] == 10500.0
        assert row["consecutive_losses"] == 2

    def test_state_roundtrip(self, tmp_path):
        """BotState can be serialized to dict and back."""
        original = BotState(account_balance_usdt=9800.0, kill_switch_activated=True)
        state_dict = original.to_dict()
        restored = BotState.from_dict(state_dict)

        assert restored.account_balance_usdt == original.account_balance_usdt
        assert restored.kill_switch_activated == original.kill_switch_activated


class TestRestartRecovery:
    """Verify state survives restart."""

    def test_botstate_persistence_methods(self):
        """BotState has save_to_db and load_from_db methods."""
        assert hasattr(BotState, "save_to_db"), "BotState must have save_to_db"
        assert hasattr(BotState, "load_from_db"), "BotState must have load_from_db"
        assert hasattr(BotState, "to_dict"), "BotState must have to_dict"
        assert hasattr(BotState, "from_dict"), "BotState must have from_dict"

    def test_botstate_from_dict_restores_all_fields(self):
        """from_dict must restore all state fields."""
        state = BotState(
            account_balance_usdt=10_000.0,
            consecutive_losses=3,
            kill_switch_activated=True,
            trigger_state=TriggerState.WAITING_TRIGGER,
        )
        data = state.to_dict()
        restored = BotState.from_dict(data)

        assert restored.account_balance_usdt == 10_000.0
        assert restored.consecutive_losses == 3
        assert restored.kill_switch_activated is True


class TestSecurity:
    """Verify secret safety."""

    def test_gitignore_exists(self):
        """.gitignore must exist to prevent secret leaks."""
        assert os.path.exists(".gitignore"), ".gitignore must exist"
        with open(".gitignore") as f:
            content = f.read()
        assert ".env" in content, ".gitignore must exclude .env"
        assert "*.db" in content, ".gitignore must exclude database files"
        assert "journal_logs" in content, ".gitignore must exclude journal logs"

    def test_env_example_has_dummy_values(self):
        """.env.example must have placeholder values, not real secrets."""
        assert os.path.exists(".env.example")
        with open(".env.example") as f:
            content = f.read()
        assert "your_binance_api_key_here" in content
        assert "your_binance_api_secret_here" in content

    def test_security_manager_masks_secrets(self):
        """SecurityManager must mask secrets properly."""
        from core.security import SecurityManager
        masked = SecurityManager.mask_secret("BINANCE_API_KEY_123456789")
        assert "123456789" not in masked
        assert masked == "BIN***789"


class TestDataIntegrity:
    """Verify data quality checks."""

    def test_data_health_safe_returns_safe(self):
        """Valid data must pass validation."""
        engine = DataHealthEngine()
        candles = []
        for i in range(70):
            candles.append(
                Candle(
                    timestamp=1000000 + i * 300000,
                    open=100.0 + i, high=105.0 + i, low=95.0 + i,
                    close=102.0 + i, volume=10.0, is_closed=True,
                )
            )
        status, reason = engine.validate_candles("5m", candles)
        assert status == DataSafetyStatus.SAFE

    def test_data_health_rejects_open_candles(self):
        """Open candles must be rejected."""
        engine = DataHealthEngine()
        candles = []
        for i in range(70):
            candles.append(
                Candle(
                    timestamp=1000000 + i * 300000,
                    open=100.0 + i, high=105.0 + i, low=95.0 + i,
                    close=102.0 + i, volume=10.0, is_closed=True,
                )
            )
        candles[-1] = Candle(
            timestamp=1000000 + 69 * 300000,
            open=100.0, high=105.0, low=95.0, close=102.0, volume=10.0, is_closed=False,
        )
        status, reason = engine.validate_candles("5m", candles)
        assert status == DataSafetyStatus.UNSAFE

    def test_data_health_result_object(self):
        """evaluate_health returns DataHealthResult object."""
        engine = DataHealthEngine()
        candles = []
        for i in range(70):
            candles.append(
                Candle(
                    timestamp=1000000 + i * 300000,
                    open=100.0, high=105.0, low=95.0, close=102.0, volume=10.0, is_closed=True,
                )
            )
        candles_dict = {tf: candles for tf in ["5m", "15m", "1h", "4h"]}
        result = engine.evaluate_health(candles_dict, coinglass_available=True, cmc_available=True)
        assert isinstance(result, DataHealthResult)
        assert result.overall_safety == DataSafetyStatus.SAFE