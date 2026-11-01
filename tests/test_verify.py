from value_engine.ai.schemas import Category, RedFlag, Severity
from value_engine.ai.verify import quote_on_page, verify_flags

PAGE = ("Note 30 Contingent liabilities\nClaims against the Company not acknowledged as debt "
        "amount to ₹ 1,234 crore, mainly in-\ncome-tax demands under appeal.")


def _flag(quote: str, page: int) -> RedFlag:
    return RedFlag(category=Category.CONTINGENT_LIABILITY, severity=Severity.HIGH, title="Tax disputes",
                   explanation="x", evidence_quote=quote, page=page, amount_cr=1234.0)


def test_quote_survives_pdf_extraction_noise():
    # The PDF splits "income-tax" across a line break ("in-\ncome-tax"); Claude adds curly quotes
    assert quote_on_page("“mainly income-tax demands under appeal”", PAGE)
    assert quote_on_page("not acknowledged as debt amount to ₹ 1,234 crore, mainly income-tax demands", PAGE)


def test_invented_quote_is_rejected():
    assert not quote_on_page("The auditor resigned citing lack of access to records", PAGE)
    assert not quote_on_page("debt", PAGE)  # too short to prove anything


def test_wrong_page_is_corrected_but_unseen_pages_do_not_count():
    pages = ["intro", PAGE, "other"]
    [result] = verify_flags([_flag("Claims against the Company not acknowledged as debt", 3)], pages, [2, 3])
    assert result.verified and result.page == 2
    [unseen] = verify_flags([_flag("Claims against the Company not acknowledged as debt", 2)], pages, [1, 3])
    assert not unseen.verified  # page 2 was not in the excerpt Claude received
