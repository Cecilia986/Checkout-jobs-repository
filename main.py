import os
import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
from companies import companies

# Load environment variables
load_dotenv()

EMAIL_SENDER = os.getenv("EMAIL_SENDER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_RECEIVER = os.getenv("EMAIL_RECEIVER")

# 1. Expanded keyword list to ensure coverage of various engineering roles for Mastercard and Revolut
KEYWORDS = [
    "performance test",
    "automation",
    "sdet",
    "qa automation",
    "test engineer",
    "quality assurance",
    "software engineer",  # Ensures catching standard software engineers
    "engineer"           # Fallback safety keyword
]

SENT_FILE = "sent_jobs.json"

# Load historical deduplication registry
if os.path.exists(SENT_FILE):
    with open(SENT_FILE, "r", encoding="utf-8") as f:
        sent_jobs = json.load(f)
else:
    sent_jobs = []

new_jobs = []

def keyword_match(text, keywords):
    text = text.lower()
    return any(keyword.lower() in text for keyword in keywords)

# --- AUTOMATED SCRAPING WORKFLOW ---
print("Initializing Playwright automation environment...")
with sync_playwright() as p:
    # Added user_agent for modern job portals to simulate a real browser, preventing blocks or incomplete loading
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    page = context.new_page()

    for company in companies:
        try:
            print(f"\nChecking {company['name']} career site...")
            # Extended timeout to 60 seconds to prevent crashes from slow page loads like Revolut
            page.goto(company["url"], wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(6000)  # Provide ample time for asynchronous components to render
            
            # Automatically handle cookie consent banners to prevent layout overlays from blocking text parsing
            try:
                cookie_selector = (
                    "button:has-text('Accept All'), "
                    "button:has-text('Accept Cookies'), "
                    "button:has-text('Accept'), "
                    "button:has-text('Agree'), "
                    "button:has-text('Allow all'), "
                    "button:has-text('Choose your cookies')"
                )
                accept_button = page.locator(cookie_selector).first
                if accept_button.is_visible(timeout=3000):
                    print(f"  Cookie banner detected for {company['name']}. Automatically clicking accept...")
                    accept_button.click()
                    page.wait_for_timeout(2000)
            except Exception:
                pass

            # Simulate scrolling down slightly to trigger lazy-loading (crucial for long listings like Revolut and Mastercard)
            page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2);")
            page.wait_for_timeout(2000)

            # Strategy A: Identify standard job board card containers (compatible with Mastercard's Eightfold/Workday styles and Revolut structures)
            job_cards_selector = "li.jobs-list-item, div.job-card, div.job-description, [role='listitem'], div[class*='styles__Card'], div[class*='jobCard']"
            cards = page.locator(job_cards_selector)
            card_count = cards.count()

            if card_count > 0:
                print(f"  Standard job card component identified: Processing {card_count} sections...")
                for j in range(card_count):
                    try:
                        card = cards.nth(j)
                        card_text = card.inner_text().strip()
                        
                        if not card_text:
                            continue

                        # Extract the first line as the job title
                        title = card_text.split("\n")[0].strip()

                        # Attempt to isolate specific unique hyperlinks inside the card
                        card_links = card.locator("a")
                        href = None
                        if card_links.count() > 0:
                            for k in range(card_links.count()):
                                possible_href = card_links.nth(k).get_attribute("href")
                                if possible_href and any(x in possible_href.lower() for x in ["job", "requisition", "careers", "position"]):
                                    href = possible_href
                                    break
                            if not href:
                                href = card_links.first.get_attribute("href")

                        # If no link can be extracted, fall back to the current company URL to ensure the job listing isn't dropped
                        if not href:
                            href = company["url"]

                        if keyword_match(card_text, KEYWORDS):
                            if href.startswith("/"):
                                base_url = company["url"].rstrip("/")
                                href = base_url + href
                            
                            # [CRITICAL FIX]: Using Company Name + Job Title as the unique tracking key!
                            # This ensures multiple listings with the same landing URL are processed individually instead of being filtered out.
                            unique_job_key = f"{company['name']}-{title}".strip().lower()

                            # Always append matching roles to the notification list per your rule requirements
                            print(f"  Successfully matched new job [{company['name']}]: {title}")
                            new_jobs.append({
                                "company": company["name"],
                                "title": title,
                                "link": href
                            })
                            
                            if unique_job_key not in sent_jobs:
                                sent_jobs.append(unique_job_key)
                    except Exception as card_err:
                        continue
            else:
                # Strategy B: Fallback universal link (anchor tag) traversal strategy if cards are not structurally resolved
                print("  No standard card layout detected. Initializing universal anchor tag text scan...")
                links = page.locator("a")
                count = links.count()
                print(f"  Found {count} raw hyperlinks on the page. Beginning text evaluation...")

                for i in range(count):
                    try:
                        link_element = links.nth(i)
                        href = link_element.get_attribute("href")
                        title = link_element.inner_text().strip()

                        if not title or not href:
                            continue

                        if keyword_match(title, KEYWORDS):
                            if href.startswith("/"):
                                base_url = company["url"].rstrip("/")
                                href = base_url + href
                            elif not href.startswith("http"):
                                continue

                            unique_job_key = f"{company['name']}-{title}".strip().lower()

                            print(f" Successfully matched new job [{company['name']}]: {title}")
                            new_jobs.append({
                                "company": company["name"],
                                "title": title,
                                "link": href
                            })
                            
                            if unique_job_key not in sent_jobs:
                                sent_jobs.append(unique_job_key)
                    except Exception:
                        continue

        except Exception as e:
            print(f" Exception occurred while checking {company['name']}: {e}")

    browser.close()

# Update and save the historical deduplication tracking registry cache log
with open(SENT_FILE, "w", encoding="utf-8") as f:
    json.dump(sent_jobs, f, indent=2, ensure_ascii=False)


# --- EMAIL NOTIFICATION WORKFLOW ---
print("\nAssembling email packaging metadata...")
if new_jobs:
    body = f" Hi! The system found {len(new_jobs)} matching localized roles in Ireland today:\n\n"
    for idx, job in enumerate(new_jobs, 1):
        body += f"{idx}.  Company: {job['company']}\n"
        body += f"    Title: {job['title']}\n"
        body += f"    Apply Link: {job['link']}\n"
        body += "\n----------------------------------------\n\n"
    subject = f"[Job Alert] Today's Ireland R&D/Testing Tracks: {len(new_jobs)} New Matches"
else:
    body = "No new roles matching your career tracking filters were found on your monitored portals today."
    subject = "[Job Alert] Today's Tracking: 0 New Matches Found"

msg = MIMEMultipart()
msg["From"] = EMAIL_SENDER
msg["To"] = EMAIL_RECEIVER
msg["Subject"] = subject
msg.attach(MIMEText(body, "plain", "utf-8"))

try:
    print("Connecting to secure Gmail SMTP server...")
    server = smtplib.SMTP("smtp.gmail.com", 587)
    server.starttls()
    server.login(EMAIL_SENDER, EMAIL_PASSWORD)
    
    print("Dispatching email notifications...")
    server.send_message(msg)
    server.quit()
    print(" Success! Your aggregated multi-match data reports have been successfully dispatched to your inbox.")
except Exception as email_err:
    print(f" Failed to dispatch email update alerts: {email_err}")