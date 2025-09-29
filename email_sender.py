# email_sender.py

import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import datetime

def format_html_body(summaries):
    """Formats the list of summaries into a clean HTML email body."""
    # Start with a simple header
    html = """
    <html>
      <head>
        <style>
          body { font-family: sans-serif; }
          h1 { color: #333; }
          h2 { color: #555; }
          a { color: #0066cc; text-decoration: none; }
          p { line-height: 1.6; }
          .article { margin-bottom: 2em; border-bottom: 1px solid #eee; padding-bottom: 1em; }
        </style>
      </head>
      <body>
        <h1>Your Daily News Summary</h1>
    """

    # Add each article summary
    if not summaries:
        html += "<p>No news summaries were generated today.</p>"
    else:
        for article in summaries:
            # Sanitize title and summary to prevent HTML issues
            title = article.get('title', 'No Title').replace('<', '&lt;').replace('>', '&gt;')
            summary = article.get('summary', 'No summary available.').replace('<', '&lt;').replace('>', '&gt;')
            url = article.get('url', '#')
            
            html += f"""
            <div class="article">
              <h2><a href="{url}">{title}</a></h2>
              <p>{summary}</p>
            </div>
            """

    # Add a footer
    html += """
        <hr>
        <p style="font-size: 0.8em; color: #777;">
          This email was generated automatically by the Python News Summarizer.
        </p>
      </body>
    </html>
    """
    return html

def send_summary_email(summaries, sender_email, sender_password, recipient_email):
    """
    Sends the formatted news summaries to the recipient's email address.

    Args:
        summaries (list): A list of dictionaries, each containing 'title', 'summary', and 'url'.
        sender_email (str): The email address to send from.
        sender_password (str): The App Password for the sender's email account.
        recipient_email (str): The email address of the recipient.
    """
    # Create the email message object
    message = MIMEMultipart("alternative")
    today_str = datetime.date.today().strftime('%B %d, %Y')
    message["Subject"] = f"Your Daily News Summary - {today_str}"
    message["From"] = sender_email
    message["To"] = recipient_email

    # Format the HTML body and attach it
    html_body = format_html_body(summaries)
    message.attach(MIMEText(html_body, "html"))

    # Create a secure SSL context
    context = ssl.create_default_context()

    # Try to connect to the server and send the email
    try:
        # We'll use Gmail's SMTP server as an example
        smtp_server = "smtp.gmail.com"
        port = 465  # For SSL
        
        print("\n📧 Connecting to email server...")
        with smtplib.SMTP_SSL(smtp_server, port, context=context) as server:
            server.login(sender_email, sender_password)
            print("Login successful. Sending email...")
            server.sendmail(sender_email, recipient_email, message.as_string())
            print("✅ Email sent successfully!")
            
    except smtplib.SMTPAuthenticationError:
        print("❌ SMTP ERROR: Authentication failed. Please check your SENDER_EMAIL and SENDER_PASSWORD (App Password).")
    except Exception as e:
        print(f"❌ An unexpected error occurred while sending the email: {e}")