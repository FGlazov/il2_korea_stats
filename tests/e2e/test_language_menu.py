"""The footer language menu (TD-24): flags, keyboard use, no horizontal overflow at 360 px, the choice sticks."""

from playwright.sync_api import Page, expect


def test_the_language_menu_opens_by_keyboard_and_switches(page: Page) -> None:
    page.set_viewport_size({"width": 360, "height": 800})
    page.goto("/")
    menu = page.locator("details.language-menu")
    summary = menu.locator("summary")

    expect(summary).to_contain_text("English")
    summary.focus()
    page.keyboard.press("Enter")
    expect(menu).to_have_attribute("open", "")
    german = menu.get_by_role("link", name="Deutsch")
    expect(german).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    assert page.evaluate(
        "[...document.querySelectorAll('.language-menu img')].every(i => i.getAttribute('alt') === '')"
    )

    german.click()

    expect(page.locator("details.language-menu summary")).to_contain_text("Deutsch")
    expect(page.locator("html")).to_have_attribute("lang", "de")
