from AlgorithmImports import *

from datetime import date, time, timedelta
import math
import statistics

from signal_manifest import SIGNAL_MANIFEST


class BidAskOptionFillModel(ImmediateFillModel):
    """Force option market buys to the ask and sells to the bid."""

    def market_fill(self, asset, order):
        fill = super().market_fill(asset, order)
        if order.direction == OrderDirection.BUY and asset.ask_price > 0:
            fill.fill_price = asset.ask_price
        elif order.direction == OrderDirection.SELL and asset.bid_price > 0:
            fill.fill_price = asset.bid_price
        return fill


class PerContractFeeModel(FeeModel):
    def __init__(self, fee_per_contract):
        self.fee_per_contract = fee_per_contract

    def get_order_fee(self, parameters):
        fee = abs(parameters.order.quantity) * self.fee_per_contract
        return OrderFee(CashAmount(fee, "USD"))


class SoxlWr2Cci5LongCall(QCAlgorithm):
    """Frozen Tradier SOXL signals executed with historical option NBBO data.

    Primary protocol:
      * calls, 30-45 DTE, expiry nearest 37 DTE, ATM strike
      * select at/after 09:35 ET and trade on a fresh quote at/after 09:36 ET
      * market buy at ask; market sell at bid
      * 100%/50% QQQ EMA200 regime mapped to delta-equivalent SOXL exposure
      * premium cap of 20%/10% of equity, respectively
    """

    PERIODS = {
        "IS": (date(2016, 9, 6), date(2022, 9, 2)),
        "VALIDATION": (date(2022, 9, 6), date(2024, 8, 30)),
        "OOS": (date(2024, 9, 3), date(2026, 9, 3)),
    }

    MIN_DTE = 30
    MAX_DTE = 45
    TARGET_DTE = 37
    ROLL_DTE = 7
    FEE_PER_CONTRACT = 0.65
    QUOTE_MAX_AGE_SECONDS = 90
    ENTRY_CUTOFF = time(9, 45)

    def initialize(self):
        self.set_time_zone(TimeZones.NEW_YORK)

        period_parameter = self.get_parameter("period")
        self.period = (period_parameter or "OOS").strip().upper()
        if self.period not in self.PERIODS:
            raise ValueError(
                f"period must be one of {sorted(self.PERIODS)}; got {self.period!r}"
            )

        start, end = self.PERIODS[self.period]
        self.set_start_date(start.year, start.month, start.day)
        self.set_end_date(end.year, end.month, end.day)
        self.set_cash(100_000)
        self.set_benchmark("SOXL")
        self.set_brokerage_model(
            BrokerageName.TRADIER_BROKERAGE,
            AccountType.MARGIN,
        )

        self.soxl = self.add_equity(
            "SOXL",
            Resolution.MINUTE,
            fill_forward=False,
            leverage=1,
            extended_market_hours=False,
            data_normalization_mode=DataNormalizationMode.RAW,
        ).symbol

        option = self.add_option(
            "SOXL",
            Resolution.MINUTE,
            fill_forward=False,
            extended_market_hours=False,
        )
        self.option_symbol = option.symbol
        option.set_filter(
            lambda universe: universe.include_weeklys()
            .calls_only()
            .strikes(-10, 10)
            .expiration(timedelta(days=25), timedelta(days=50))
        )

        self.actions = {
            date.fromisoformat(row["execution_date"]): row
            for row in SIGNAL_MANIFEST[self.period]
        }
        self.processed_action_dates = set()

        self.fill_model = BidAskOptionFillModel()
        self.fee_model = PerContractFeeModel(self.FEE_PER_CONTRACT)

        self.logical_position = False
        self.regime_weight = None
        self.held_contract = None
        self.held_expiry = None
        self.held_quantity = 0
        self.entry_fill_price = None
        self.entry_fill_time = None
        self.entry_metadata = None

        self.staged_action = None
        self.candidate = None
        self.candidate_selected_at = None
        self.order_in_flight = None
        self.roll_after_exit = False
        self.corporate_action_reentry = False
        self.reentry_not_before = None
        self.partial_fill_detected = False
        self.invalid_run = False

        self.skipped_entries = 0
        self.quote_rejections = 0
        self.rolls = 0
        self.unexpected_liquidations = 0
        self.exercise_cleanups = 0
        self.trade_ledger = []
        self.fill_audit = []

        self.set_runtime_statistic("Period", self.period)
        self.set_runtime_statistic("Execution", "09:36 fresh quote")
        self.set_runtime_statistic("Option", "ATM call / 30-45 DTE")

    def on_data(self, data: Slice):
        if self.time.time() < time(9, 35) or self.time.time() > self.ENTRY_CUTOFF:
            return

        self._clean_up_exercise_shares()

        action = self.actions.get(self.time.date())
        if (
            action is not None
            and action["action"] == "SELL"
            and self.time.date() not in self.processed_action_dates
            and self.order_in_flight is None
            and self.staged_action is not None
            and self.staged_action["action"] in ("ROLL_SELL", "ROLL_BUY")
        ):
            self.staged_action = None
            self.candidate = None
            self.candidate_selected_at = None
            self.roll_after_exit = False

        if (
            action is not None
            and self.time.date() not in self.processed_action_dates
            and self.staged_action is None
            and self.order_in_flight is None
        ):
            self.processed_action_dates.add(self.time.date())
            self._stage_manifest_action(action, data)

        if (
            action is None
            and self.logical_position
            and self.held_contract is not None
            and self.staged_action is None
            and self.order_in_flight is None
        ):
            days_to_expiry = (self.held_expiry.date() - self.time.date()).days
            if days_to_expiry <= self.ROLL_DTE:
                self.staged_action = {
                    "action": "ROLL_SELL",
                    "tag": f"7-DTE roll from {self.held_contract}",
                    "staged_at": self.time,
                }
                self.roll_after_exit = True

        if (
            self.corporate_action_reentry
            and self.logical_position
            and self.held_contract is None
            and self.staged_action is None
            and self.order_in_flight is None
            and (
                self.reentry_not_before is None
                or self.time.date() >= self.reentry_not_before
            )
        ):
            self.staged_action = {
                "action": "RECOVERY_BUY",
                "regime_weight": self.regime_weight,
                "tag": "Restore logical exposure after corporate action",
                "staged_at": self.time,
            }
            self.candidate = None

        if (
            self.roll_after_exit
            and self.logical_position
            and self.held_contract is None
            and self.order_in_flight is None
        ):
            self.staged_action = {
                "action": "ROLL_BUY",
                "regime_weight": self.regime_weight,
                "tag": "7-DTE replacement call",
                "staged_at": self.time,
            }
            self.candidate = None
            self.roll_after_exit = False

        self._try_execute_staged_action(data)

    def _stage_manifest_action(self, action, data):
        side = action["action"]
        if side == "BUY":
            if self.logical_position or self.held_contract is not None:
                self.error(f"Ignored duplicate BUY manifest action: {action}")
                return
            self.logical_position = True
            self.regime_weight = float(action["regime_weight"])
            self.staged_action = {
                **action,
                "staged_at": self.time,
                "tag": (
                    f"v1 BUY trade={action['trade_id']} "
                    f"signal={action['signal_date']} weight={self.regime_weight:.2f}"
                ),
            }
            self.candidate = None
        elif side == "SELL":
            self.logical_position = False
            if self.held_contract is None:
                self.error(f"SELL manifest action found while flat: {action}")
                return
            self.staged_action = {
                **action,
                "staged_at": self.time,
                "tag": (
                    f"v1 SELL trade={action['trade_id']} "
                    f"signal={action['signal_date']} {action['exit_reason']}"
                ),
            }

    def _try_execute_staged_action(self, data):
        if self.staged_action is None or self.order_in_flight is not None:
            return

        kind = self.staged_action["action"]

        if kind in ("BUY", "ROLL_BUY", "RECOVERY_BUY"):
            if self.time.time() >= self.ENTRY_CUTOFF:
                self.skipped_entries += 1
                self.error(f"Skipped {kind}: no valid fresh option quote by 09:45")
                if kind == "BUY":
                    self.logical_position = False
                else:
                    self.corporate_action_reentry = True
                    self.reentry_not_before = self.time.date() + timedelta(days=1)
                self.staged_action = None
                self.candidate = None
                return

            if self.candidate is None:
                self.candidate = self._select_contract(data)
                if self.candidate is not None:
                    self.candidate_selected_at = self.time
                return

            if self.time <= self.candidate_selected_at:
                return

            quote = self._fresh_quote(data, self.candidate.symbol)
            if quote is None:
                return

            weight = float(self.staged_action["regime_weight"])
            quantity, sizing = self._entry_quantity(self.candidate, quote, weight)
            if quantity < 1:
                self.skipped_entries += 1
                self.error(f"Skipped {kind}: sizing produced zero contracts")
                if kind == "BUY":
                    self.logical_position = False
                else:
                    self.corporate_action_reentry = True
                    self.reentry_not_before = self.time.date() + timedelta(days=1)
                self.staged_action = None
                self.candidate = None
                return

            security = self.securities[self.candidate.symbol]
            security.set_fill_model(self.fill_model)
            security.set_fee_model(self.fee_model)

            expected_ask = float(quote.ask.close)
            tag = f"{self.staged_action['tag']} | {sizing}"
            ticket = self.market_order(
                self.candidate.symbol,
                quantity,
                asynchronous=True,
                tag=tag,
            )
            self.order_in_flight = {
                "order_id": ticket.order_id,
                "kind": kind,
                "expected_side": "ask",
                "expected_price": expected_ask,
                "metadata": dict(self.staged_action),
                "contract": self.candidate,
                "quantity": quantity,
                "entry_snapshot": {
                    "dte": (self.candidate.expiry.date() - self.time.date()).days,
                    "strike": float(self.candidate.strike),
                    "spot": float(self.securities[self.soxl].price),
                    "delta": self._finite_or_none(self.candidate.greeks.delta),
                    "iv": self._finite_or_none(self.candidate.implied_volatility),
                    "open_interest": int(self.candidate.open_interest),
                    "bid": float(quote.bid.close),
                    "ask": expected_ask,
                    "spread": expected_ask - float(quote.bid.close),
                },
            }
            return

        if kind in ("SELL", "ROLL_SELL"):
            if self.time <= self.staged_action["staged_at"]:
                if self.time.time() >= self.ENTRY_CUTOFF:
                    self.invalid_run = True
                    self.error(f"Could not delay {kind} until after its 09:45 staging time")
                    self.quit("Required exit could not execute in its intended session")
                return
            quote = self._fresh_quote(data, self.held_contract)
            if quote is None:
                if self.time.time() >= self.ENTRY_CUTOFF:
                    self.invalid_run = True
                    self.error(f"No fresh quote to complete {kind} by 09:45")
                    self.quit("Required exit quote missing; results are not valid")
                return
            expected_bid = float(quote.bid.close)
            ticket = self.market_order(
                self.held_contract,
                -abs(self.held_quantity),
                asynchronous=True,
                tag=self.staged_action["tag"],
            )
            self.order_in_flight = {
                "order_id": ticket.order_id,
                "kind": kind,
                "expected_side": "bid",
                "expected_price": expected_bid,
                "metadata": dict(self.staged_action),
                "contract": self.held_contract,
                "quantity": abs(self.held_quantity),
            }

    def _select_contract(self, data):
        chain = data.option_chains.get(self.option_symbol)
        if chain is None:
            return None

        spot = float(self.securities[self.soxl].price)
        if spot <= 0:
            return None

        eligible = []
        for contract in chain:
            dte = (contract.expiry.date() - self.time.date()).days
            if contract.right != OptionRight.CALL or not self.MIN_DTE <= dte <= self.MAX_DTE:
                continue
            security = self.securities[contract.symbol]
            multiplier = float(security.symbol_properties.contract_multiplier)
            if multiplier != 100:
                continue
            if contract.bid_price <= 0 or contract.ask_price <= 0:
                continue
            if contract.ask_price < contract.bid_price:
                continue
            eligible.append(contract)

        if not eligible:
            return None

        target_expiry = min(
            {contract.expiry for contract in eligible},
            key=lambda expiry: (
                abs((expiry.date() - self.time.date()).days - self.TARGET_DTE),
                -(expiry.date() - self.time.date()).days,
            ),
        )
        expiry_contracts = [c for c in eligible if c.expiry == target_expiry]
        return min(expiry_contracts, key=lambda c: (abs(float(c.strike) - spot), float(c.strike)))

    def _fresh_quote(self, data, symbol):
        quote = data.quote_bars.get(symbol)
        if quote is None or quote.bid is None or quote.ask is None:
            self.quote_rejections += 1
            return None
        bid = float(quote.bid.close)
        ask = float(quote.ask.close)
        age = abs((self.time - quote.end_time).total_seconds())
        if bid <= 0 or ask <= 0 or ask < bid or age > self.QUOTE_MAX_AGE_SECONDS:
            self.quote_rejections += 1
            return None
        return quote

    def _entry_quantity(self, contract, quote, regime_weight):
        equity = float(self.portfolio.total_portfolio_value)
        cash = float(self.portfolio.cash)
        spot = float(self.securities[self.soxl].price)
        ask = float(quote.ask.close)

        try:
            delta = abs(float(contract.greeks.delta))
        except (AttributeError, TypeError, ValueError):
            delta = float("nan")
        if not math.isfinite(delta) or delta < 0.05:
            delta = 0.50

        delta_target = regime_weight * equity
        delta_quantity = math.floor(delta_target / (100 * delta * spot))

        premium_fraction = 0.20 if regime_weight >= 1.0 else 0.10
        all_in_cost = 100 * ask + self.FEE_PER_CONTRACT
        premium_quantity = math.floor((premium_fraction * equity) / all_in_cost)
        cash_quantity = math.floor(cash / all_in_cost)
        quantity = max(0, min(delta_quantity, premium_quantity, cash_quantity))

        sizing = (
            f"delta={delta:.3f} premium_cap={premium_fraction:.0%} "
            f"ask={ask:.2f} dte={(contract.expiry.date() - self.time.date()).days}"
        )
        return quantity, sizing

    @staticmethod
    def _finite_or_none(value):
        try:
            number = float(value)
            return number if math.isfinite(number) else None
        except (TypeError, ValueError):
            return None

    def on_order_event(self, order_event: OrderEvent):
        active = self.order_in_flight
        if (
            active is not None
            and order_event.order_id == active["order_id"]
            and order_event.status == OrderStatus.PARTIALLY_FILLED
        ):
            self.partial_fill_detected = True
            self.error(f"Partial fill invalidates this backtest: {order_event}")
            self.quit("Partial fill encountered; results are not valid")
            return

        if (
            active is not None
            and order_event.order_id == active["order_id"]
            and order_event.status in (OrderStatus.CANCELED, OrderStatus.INVALID)
        ):
            self.error(f"Order did not fill: {order_event}")
            self.order_in_flight = None
            if active["kind"] in ("BUY", "ROLL_BUY", "RECOVERY_BUY"):
                if active["kind"] == "BUY":
                    self.logical_position = False
                else:
                    self.corporate_action_reentry = True
                    self.reentry_not_before = self.time.date() + timedelta(days=1)
                self.staged_action = None
                self.candidate = None
                self.skipped_entries += 1
            else:
                self.invalid_run = True
                self.error("Required exit order was canceled or invalid")
                self.quit("Required exit order did not fill; results are not valid")
            return

        if order_event.status not in (OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED):
            return

        if active is None or order_event.order_id != active["order_id"]:
            if (
                order_event.direction == OrderDirection.SELL
                and self.held_contract is not None
                and order_event.symbol == self.held_contract
            ):
                if order_event.status == OrderStatus.PARTIALLY_FILLED:
                    self.partial_fill_detected = True
                    self.invalid_run = True
                    self.error(
                        f"Unexpected partial option liquidation invalidates this backtest: {order_event}"
                    )
                    self.quit("Unexpected partial liquidation; results are not valid")
                    return
                if active is not None:
                    self.invalid_run = True
                    self.error(
                        "Unexpected option liquidation arrived while another order was active"
                    )
                    self.quit("Conflicting option fills; results are not valid")
                    return
                self.unexpected_liquidations += 1
                self.error(f"Unexpected option liquidation: {order_event}")
                should_reenter = self.logical_position
                reason = "external option liquidation"
                if self.staged_action is not None:
                    reason = self.staged_action.get("exit_reason", reason)
                self._record_exit(float(order_event.fill_price), reason)
                self.staged_action = None
                self.candidate = None
                self.candidate_selected_at = None
                self.order_in_flight = None
                self.roll_after_exit = False
                if should_reenter:
                    self.corporate_action_reentry = True
                    self.reentry_not_before = self.time.date() + timedelta(days=1)
                else:
                    self.corporate_action_reentry = False
                    self.reentry_not_before = None
            return

        expected = active["expected_price"]
        actual = float(order_event.fill_price)
        self.fill_audit.append(
            {
                "time": self.time.isoformat(),
                "order_id": order_event.order_id,
                "side": active["expected_side"],
                "expected": expected,
                "actual": actual,
                "difference": actual - expected,
            }
        )

        if order_event.status != OrderStatus.FILLED:
            return

        kind = active["kind"]
        metadata = active["metadata"]

        if kind in ("BUY", "ROLL_BUY", "RECOVERY_BUY"):
            self.held_contract = active["contract"].symbol
            self.held_expiry = active["contract"].expiry
            self.held_quantity = active["quantity"]
            self.entry_fill_price = actual
            self.entry_fill_time = self.time
            self.entry_metadata = {
                **metadata,
                **active["entry_snapshot"],
            }
            if kind == "ROLL_BUY":
                self.rolls += 1
            self.corporate_action_reentry = False
            self.reentry_not_before = None
        else:
            self._record_exit(actual, metadata.get("exit_reason", kind))

        self.staged_action = None
        self.candidate = None
        self.candidate_selected_at = None
        self.order_in_flight = None

    def _record_exit(self, exit_price, exit_reason):
        entry_price = self.entry_fill_price
        gross_return = (
            exit_price / entry_price - 1
            if entry_price and entry_price > 0
            else float("nan")
        )
        one_contract_entry_cost = (
            100 * entry_price + self.FEE_PER_CONTRACT
            if entry_price is not None
            else 0
        )
        one_contract_exit_value = 100 * exit_price - self.FEE_PER_CONTRACT
        net_return = (
            one_contract_exit_value / one_contract_entry_cost - 1
            if one_contract_entry_cost > 0
            else float("nan")
        )
        details = self.entry_metadata or {}
        self.trade_ledger.append(
            {
                "entry_time": (
                    self.entry_fill_time.isoformat()
                    if self.entry_fill_time is not None
                    else None
                ),
                "exit_time": self.time.isoformat(),
                "contract": str(self.held_contract),
                "quantity": self.held_quantity,
                "entry_ask": entry_price,
                "exit_bid": exit_price,
                "gross_option_return": gross_return,
                "option_return": net_return,
                "regime_weight": self.regime_weight,
                "exit_reason": exit_reason,
                "entry_dte": details.get("dte"),
                "entry_strike": details.get("strike"),
                "entry_spot": details.get("spot"),
                "entry_delta": details.get("delta"),
                "entry_iv": details.get("iv"),
                "entry_open_interest": details.get("open_interest"),
                "entry_bid": details.get("bid"),
                "entry_spread": details.get("spread"),
            }
        )
        self.held_contract = None
        self.held_expiry = None
        self.held_quantity = 0
        self.entry_fill_price = None
        self.entry_fill_time = None
        self.entry_metadata = None

    def _clean_up_exercise_shares(self):
        shares = self.portfolio[self.soxl].quantity
        if shares == 0:
            return
        self.exercise_cleanups += 1
        self.market_order(self.soxl, -shares, tag="Immediate cleanup of exercised SOXL shares")

    def on_end_of_algorithm(self):
        audited = len(self.fill_audit)
        mismatches = sum(
            abs(row["difference"]) > 0.01 for row in self.fill_audit
        )
        option_returns = [
            row["option_return"]
            for row in self.trade_ledger
            if math.isfinite(row["option_return"])
        ]
        wins = [value for value in option_returns if value > 0]
        losses = [value for value in option_returns if value < 0]
        win_rate = len(wins) / len(option_returns) if option_returns else float("nan")
        profit_factor = (
            sum(wins) / abs(sum(losses))
            if losses
            else (float("inf") if wins else float("nan"))
        )
        average_return = statistics.fmean(option_returns) if option_returns else float("nan")
        median_return = statistics.median(option_returns) if option_returns else float("nan")
        worst_return = min(option_returns) if option_returns else float("nan")

        self.set_runtime_statistic("Closed trades", str(len(self.trade_ledger)))
        self.set_runtime_statistic("Option win rate", self._format_percent(win_rate))
        self.set_runtime_statistic("Option profit factor", self._format_number(profit_factor))
        self.set_runtime_statistic("Mean option return", self._format_percent(average_return))
        self.set_runtime_statistic("Median option return", self._format_percent(median_return))
        self.set_runtime_statistic("Worst option return", self._format_percent(worst_return))
        self.set_runtime_statistic("Skipped entries", str(self.skipped_entries))
        self.set_runtime_statistic("Rolls", str(self.rolls))
        self.set_runtime_statistic("Fill audit", f"{audited - mismatches}/{audited} NBBO")

        self.log(
            "SUMMARY|"
            f"period={self.period}|closed_trades={len(self.trade_ledger)}|"
            f"option_win_rate={win_rate}|option_profit_factor={profit_factor}|"
            f"mean_option_return={average_return}|median_option_return={median_return}|"
            f"worst_option_return={worst_return}|"
            f"skipped_entries={self.skipped_entries}|quote_rejections={self.quote_rejections}|"
            f"rolls={self.rolls}|unexpected_liquidations={self.unexpected_liquidations}|"
            f"exercise_cleanups={self.exercise_cleanups}|fill_mismatches={mismatches}"
            f"|partial_fill_detected={self.partial_fill_detected}"
            f"|invalid_run={self.invalid_run}"
        )
        for trade in self.trade_ledger:
            self.log(f"TRADE|{trade}")
        for audit in self.fill_audit:
            if abs(audit["difference"]) > 0.01:
                self.error(f"FILL_MISMATCH|{audit}")

    @staticmethod
    def _format_percent(value):
        return "n/a" if not math.isfinite(value) else f"{value:.2%}"

    @staticmethod
    def _format_number(value):
        if math.isinf(value):
            return "inf"
        return "n/a" if not math.isfinite(value) else f"{value:.2f}"
