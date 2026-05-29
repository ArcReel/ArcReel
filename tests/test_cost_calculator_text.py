from lib.cost_calculator import CostCalculator


class TestTextCost:
    def setup_method(self):
        self.calc = CostCalculator()

    def _text(self, provider: str):
        return self.calc.calculate_cost(provider, "text", input_tokens=1000, output_tokens=500)

    def test_gemini_cost(self):
        amount, currency = self._text("gemini")
        assert currency == "USD"
        assert amount == (1000 * 0.50 + 500 * 3.00) / 1_000_000

    def test_ark_cost(self):
        amount, currency = self._text("ark")
        assert currency == "CNY"
        assert amount == (1000 * 0.60 + 500 * 3.60) / 1_000_000

    def test_grok_cost(self):
        amount, currency = self._text("grok")
        assert currency == "USD"
        assert amount == (1000 * 0.20 + 500 * 0.50) / 1_000_000

    def test_unknown_provider_defaults_to_gemini(self):
        # unknown / 裸 gemini 同走 _gemini_default_pricing_for("text") → gemini-3-flash-preview，
        # 金额须与 test_gemini_cost 一致，才证明确实回落到了 Gemini 费率而非任意 USD provider。
        amount, currency = self._text("unknown")
        assert currency == "USD"
        assert amount == (1000 * 0.50 + 500 * 3.00) / 1_000_000
