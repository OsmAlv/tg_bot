import unittest

from parsers import Marketplace, detect_marketplace
from parsers.common import parse_car_from_html
from services.price_calculator import PriceCalculator


class TestCoreLogic(unittest.TestCase):
    def test_detect_marketplace(self) -> None:
        self.assertEqual(
            detect_marketplace("https://www.encar.com/dc/dc_cardetailview.do?carid=123"),
            Marketplace.ENCAR,
        )
        self.assertEqual(
            detect_marketplace("https://www.kbchachacha.com/public/car/detail/123"),
            Marketplace.KB,
        )
        self.assertEqual(
            detect_marketplace("https://www.kcar.com/bc/detail/car/123"),
            Marketplace.KCAR,
        )
        self.assertEqual(
            detect_marketplace("https://example.com/car/123"),
            Marketplace.GENERIC,
        )

    def test_price_calculation_returns_positive_values(self) -> None:
        calculator = PriceCalculator()
        result = calculator.calculate(
            car_price_usd=20_000,
            car_year=2025,
            engine_cc=2000,
            usd_uzs=12_700,
        )

        self.assertEqual(result.price_korea_usd, 20_000)
        self.assertGreater(result.final_price_usd, result.price_korea_usd)

    def test_under_1_year_customs_match_external_bot_math(self) -> None:
        calculator = PriceCalculator()

        # Новый режим: <1 года => 30% + 2$/cc (плюс НДС, сбор, утилизация)
        # Пошлина и НДС считаются на CIF (цена + доставка)
        customs = calculator._customs_under_1_year(
            car_price_usd=110_540,
            engine_cc=2999,
            usd_uzs=12_091.22,
        )
        self.assertAlmostEqual(customs, 64_004.03, places=2)

    def test_customs_for_2026_2999cc_uses_30_and_2(self) -> None:
        calculator = PriceCalculator()

        # Кейс из обсуждения: 2026 год, 2999cc, цена $105,541.
        # Проверяем новый режим: 30% + 2$/cc, доставка +5000, с наценкой 5%.
        usd_uzs = 12_091.22
        price = 105_541
        engine_cc = 2999

        result = calculator.calculate(
            car_price_usd=price,
            car_year=2026,
            engine_cc=engine_cc,
            usd_uzs=usd_uzs,
        )

        # Регрессионная проверка текущего ожидаемого итога (с наценкой 5%)
        self.assertAlmostEqual(result.final_price_usd, 180_878.76, places=2)

    def test_customs_for_2026_3982cc_uses_30_and_2_5(self) -> None:
        calculator = PriceCalculator()

        # Кейс из @autodeklarantbot: 2026 год, 3982cc, цена $225,663.
        # Под ключ ожидается около $376,152 при фиксированном курсе 12,091.22.
        result = calculator.calculate(
            car_price_usd=225_663,
            car_year=2026,
            engine_cc=3982,
            usd_uzs=12_091.22,
        )

        self.assertAlmostEqual(result.final_price_usd, 376_152.0, delta=5.0)

    def test_duty_multiplier_affects_result(self) -> None:
        calculator = PriceCalculator()

        base = calculator.calculate(
            car_price_usd=50_000,
            car_year=2020,
            engine_cc=3000,
            usd_uzs=12_091.22,
        )
        no_duty = calculator.calculate(
            car_price_usd=50_000,
            car_year=2020,
            engine_cc=3000,
            usd_uzs=12_091.22,
            duty_multiplier=0.0,
        )
        doubled_duty = calculator.calculate(
            car_price_usd=50_000,
            car_year=2020,
            engine_cc=3000,
            usd_uzs=12_091.22,
            duty_multiplier=2.0,
        )

        self.assertLess(no_duty.final_price_usd, base.final_price_usd)
        self.assertGreater(doubled_duty.final_price_usd, base.final_price_usd)

    def test_autodeklarantbot_compatibility_without_certificate(self) -> None:
        """Проверяем совпадение с ботом @autodeklarantbot без сертификата происхождения"""
        calculator = PriceCalculator()
        
        # Данные из скриншота @autodeklarantbot
        # БЕЗ сертификата: цена $16,109, 1991cc, >3 лет
        # Режим CIF: пошлина/НДС считаются от (цена + доставка)
        customs = calculator._customs_over_3_years(
            car_price_usd=16_109,
            engine_cc=1991,
            usd_uzs=12_091.22,
            duty_multiplier=1.0,
        )
        self.assertAlmostEqual(customs, 42_067.06, delta=100)

    def test_autodeklarantbot_compatibility_with_certificate(self) -> None:
        """Проверяем совпадение с ботом @autodeklarantbot с сертификатом происхождения"""
        calculator = PriceCalculator()

        # Данные из скриншота @autodeklarantbot
        # С сертификатом (duty_multiplier=0.5): цена $16,109, 1991cc, >3 лет
        # Режим CIF: пошлина/НДС считаются от (цена + доставка)
        customs = calculator._customs_over_3_years(
            car_price_usd=16_109,
            engine_cc=1991,
            usd_uzs=12_091.22,
            duty_multiplier=0.5,
        )
        self.assertAlmostEqual(customs, 25_920.46, delta=100)

    def test_generic_parser_extracts_price_from_json_ld(self) -> None:
        html = """
        <html>
            <head>
                <title>Audi A6</title>
                <script type="application/ld+json">
                    {
                        "@context": "https://schema.org",
                        "@type": "Product",
                        "name": "Audi A6 2021",
                        "offers": {
                            "@type": "Offer",
                            "priceCurrency": "USD",
                            "price": "25 500"
                        }
                    }
                </script>
            </head>
            <body>Пробег 50 000 км двигатель 2.0 l бензин</body>
        </html>
        """

        car = parse_car_from_html(html, "https://example.com/car/123", strict=False)

        self.assertEqual(car.year, 2021)
        self.assertEqual(car.mileage_km, 50_000)
        self.assertEqual(car.engine_cc, 2000)
        self.assertEqual(car.price_won, 25_500)
        self.assertEqual(car.price_currency, "USD")


if __name__ == "__main__":
    unittest.main()