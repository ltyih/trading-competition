"""Tests for RIT client (using mock)"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.rit_client import Mock*REMOVED*lient
from src.models import CaseState, Position, TenderOffer


def test_mock_client_creation():
    """Test mock client creation"""
    client = Mock*REMOVED*lient(
        host="localhost",
        port=9999,
        api_key="test_key",
        trader_id="UBCT-P"
    )

    assert client.trader_id == "UBCT-P"
    assert client.is_connected()

    print("Mock client creation test passed!")


def test_mock_get_case():
    """Test mock case retrieval"""
    client = Mock*REMOVED*lient("localhost", 9999, "key", "UBCT-P")

    client.set_mock_tick(90)
    case = client.get_case()

    assert isinstance(case, CaseState)
    assert case.tick == 90
    assert case.current_day == 1  # 90 // 180 + 1 = 1
    assert case.time_in_day == 90  # 90 % 180 = 90

    client.set_mock_tick(200)
    case = client.get_case()
    assert case.current_day == 2  # 200 // 180 + 1 = 2
    assert case.time_in_day == 20  # 200 % 180 = 20

    print("Mock get_case test passed!")


def test_mock_positions():
    """Test mock position tracking"""
    client = Mock*REMOVED*lient("localhost", 9999, "key", "UBCT-P")

    client.set_mock_positions({
        'ELEC-F': -10,
        'NG': 24,
        'ELEC-day2': 5
    })

    securities = client.get_securities()
    assert len(securities) == 3

    # Check specific position
    elec_f = client.get_securities(ticker='ELEC-F')
    assert len(elec_f) == 1
    assert elec_f[0].quantity == -10

    print("Mock positions test passed!")


def test_mock_news():
    """Test mock news feed"""
    client = Mock*REMOVED*lient("localhost", 9999, "key", "UBCT-P")

    client.add_mock_news({
        'news_id': 1,
        'tick': 90,
        'headline': 'Test News',
        'body': '12 hours of sunshine expected'
    })
    client.add_mock_news({
        'news_id': 2,
        'tick': 95,
        'headline': 'More News',
        'body': 'Temperature 28 degrees Celsius'
    })

    # Get all news
    news = client.get_news()
    assert len(news) == 2

    # Get news since ID 1
    news = client.get_news(since=1)
    assert len(news) == 1
    assert news[0]['news_id'] == 2

    print("Mock news test passed!")


def test_mock_tenders():
    """Test mock tender handling"""
    client = Mock*REMOVED*lient("localhost", 9999, "key", "UBCT-T1")
    client.set_mock_tick(100)

    client.add_mock_tender({
        'tender_id': 101,
        'ticker': 'ELEC-day2',
        'quantity': 5,
        'action': 'BUY',
        'price': 38.0,
        'expires': 200
    })
    client.add_mock_tender({
        'tender_id': 102,
        'ticker': 'ELEC-day2',
        'quantity': 3,
        'action': 'SELL',
        'price': 42.0,
        'expires': 50  # Already expired
    })

    tenders = client.get_tenders()
    assert len(tenders) == 1  # Only non-expired tender
    assert tenders[0].tender_id == 101
    assert tenders[0].action == 'BUY'

    print("Mock tenders test passed!")


def test_mock_order_book():
    """Test mock order book"""
    client = Mock*REMOVED*lient("localhost", 9999, "key", "UBCT-P")

    book = client.get_order_book('ELEC-F')

    assert book.ticker == 'ELEC-F'
    assert len(book.bids) == 2
    assert len(book.asks) == 2
    assert book.best_bid == 29.0
    assert book.best_ask == 31.0
    assert book.spread == 2.0

    print("Mock order book test passed!")


def test_mock_assets():
    """Test mock assets"""
    client = Mock*REMOVED*lient("localhost", 9999, "key", "UBCT-P")

    assets = client.get_assets()
    assert len(assets) == 1
    assert assets[0]['asset'] == 'NG_POWER_PLANT'
    assert assets[0]['quantity'] == 10

    print("Mock assets test passed!")


def test_case_state_day_calculation():
    """Test day calculation from tick"""
    # Day boundaries: 0-179 (Day 1), 180-359 (Day 2), etc.

    test_cases = [
        (0, 1),
        (179, 1),
        (180, 2),
        (181, 2),
        (359, 2),
        (360, 3),
        (540, 4),
        (720, 5),
        (899, 5),
    ]

    for tick, expected_day in test_cases:
        case = CaseState.from_api({'tick': tick, 'period': 1, 'status': 'ACTIVE'})
        assert case.current_day == expected_day, f"Tick {tick}: expected day {expected_day}, got {case.current_day}"

    print("Day calculation test passed!")


def run_all_tests():
    """Run all RIT client tests"""
    print("=" * 50)
    print("Running RIT Client Tests")
    print("=" * 50)

    test_mock_client_creation()
    test_mock_get_case()
    test_mock_positions()
    test_mock_news()
    test_mock_tenders()
    test_mock_order_book()
    test_mock_assets()
    test_case_state_day_calculation()

    print("=" * 50)
    print("All RIT client tests passed!")
    print("=" * 50)


if __name__ == '__main__':
    run_all_tests()
