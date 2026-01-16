import time
import re
import playwright.sync_api
from playwright.sync_api import sync_playwright
from helpers.exceptions import SigninError

# checkout regex pattern
url_pattern = re.compile(
    r"https://www\.recreation\.gov/permits/[^/]+/registration/[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}__\d+"
)


class Browser:
    def __init__(self):
        self.playwright = sync_playwright().start()

        self.browser = self.playwright.chromium.launch(headless=False,
                                                       args=[
                                                           "--headless=chrome",
                                                           # use the “old” headless where GPU isn’t suppressed
                                                           "--use-gl=egl",
                                                           # ask Chrome/Mesa to expose EGL instead of SwiftShader
                                                           "--enable-gpu",  # actually start the GPU process
                                                           "--enable-webgl",
                                                           "--ignore-gpu-blocklist",
                                                           "--no-sandbox",
                                                       ])
        self.context = self.browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:137.0) Gecko/20100101 Firefox/137.0"

        )

        self.page = self.context.new_page()

    def init_rec(self, username, password) -> None:
        self.page.goto("https://www.recreation.gov/")
        self.page.wait_for_load_state()

        self.page.click("#ga-global-nav-log-in-link")
        self.page.wait_for_selector("#email", state="visible", timeout=5000)

        self.page.fill("#email", username)
        self.page.fill("#rec-acct-sign-in-password", password)

        self.page.click("button.sarsa-button:nth-child(4)")

        try:
            self.page.wait_for_selector(".rec-icon-account-circle", state="visible", timeout=5000)
        except playwright.sync_api.TimeoutError:
            raise SigninError("Could not sign in to Rec.gov account")

    def reserve_date(self, date, available_permits, target_permit_id) -> None:
        if available_permits > 15:
            available_permits = 15  # max for whitney

        date = date.split("-")  # date format yyyy-mm-dd

        self.page.goto("https://www.recreation.gov/permits/445860/registration/detailed-availability")
        self.page.wait_for_load_state()

        date_selection_elements = self.page.locator(f".date-segment").all()

        for element in date_selection_elements:
            aria_label = element.get_attribute("aria-label")
            if aria_label and 'month, ' in aria_label:
                element.type(date[1])

            if aria_label and 'day, ' in aria_label:
                element.type(date[2])

        self.page.click("#guest-counter")
        self.page.wait_for_selector("#guest-counter-number-field-People", state="visible", timeout=5000)

        self.page.fill("#guest-counter-number-field-People", str(available_permits))
        self.page.click(".sarsa-dropdown-base-popup-actions-content > button:nth-child(2)")

        self.page.wait_for_selector("div.rec-grid-grid:nth-child(2) > div:nth-child(2) > div:nth-child(2) > "
                                    "div:nth-child(1) > div:nth-child(1) > p:nth-child(1) > button:nth-child(1) > "
                                    "span:nth-child(1) > span:nth-child(1)", state="visible", timeout=5000)

        permit_grid = self.page.get_by_role("grid", name="Availability by Sites and Dates")
        permit_rows = permit_grid.locator('[role="row"]').all()

        selected_row = None
        for row in permit_rows:
            grid_cells = row.locator('[role="gridcell"]')
            if grid_cells.count() == 0:
                continue  # Skip rows with no grid cells

            first_cell_text = grid_cells.first.inner_text().strip()
            if first_cell_text == target_permit_id:
                selected_row = row
                break

        if not selected_row:
            raise Exception("Could not find the selected row")

        date_elements = selected_row.locator(f".rec-availability-date").all()

        for element in date_elements:
            aria_label = element.get_attribute("aria-label")
            if aria_label and str(date[2]).lstrip("0") == re.sub(r"[^0-9]", "", aria_label.splitlines()[0]):
                element.click()

        self.page.wait_for_selector("button.sarsa-button-primary:nth-child(3)", state="visible", timeout=5000)

        self.page.click("button.sarsa-button-primary:nth-child(3)")

        self.page.wait_for_url(url_pattern)

    def cleanup(self) -> None:
        self.context.clear_cookies()

    def __del__(self):
        self.playwright.stop()


if __name__ == "__main__":
    from helpers import env
    # Sample Run
    username = env.get_string("REC_GOV_USERNAME")
    password = env.get_string("REC_GOV_PASSWORD")
    date = "2025-05-18"
    permit_id = "JM34.5"
    count = 2

    browser = Browser()
    browser.init_rec(username, password)
    browser.reserve_date(date, count, permit_id)
    print("Complete, continue checkout on Rec Web/App")
    time.sleep(5)
