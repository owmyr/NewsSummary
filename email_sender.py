import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import datetime
from urllib.parse import quote

# ============================================================
#  THEME CONFIGURATION
# ============================================================
THEME = {
    "bg_color": "#faf9f6",       # Sand
    "card_bg": "#ffffff",        # White
    "primary": "#709775",        # Sage Green
    "primary_dark": "#4a6b4e",   # Darker Sage
    "text_dark": "#292524",      # Stone Dark
    "text_light": "#78716c",     # Stone Light
    "accent": "#e5989b"          # Clay/Pink
}

def get_branded_placeholder(text):
    """Generates a placeholder image URL matching the site theme."""
    safe_text = quote(text)
    # Background: F0F4F1 (Light Sage) | Text: 709775 (Brand Sage)
    return f"https://placehold.co/600x300/F0F4F1/709775?text={safe_text}"

def format_html_body(summaries):
    """Formats the list of summaries into a responsive, branded HTML email."""
    
    today_date = datetime.date.today().strftime('%A, %B %d')
    
    # 1. EMAIL HEADER (CSS Inlined for client compatibility)
    html = f"""
    <!DOCTYPE html>
    <html>
      <head>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            /* Client-specific resets */
            body, table, td, a {{ -webkit-text-size-adjust: 100%; -ms-text-size-adjust: 100%; }}
            table, td {{ mso-table-lspace: 0pt; mso-table-rspace: 0pt; }}
            img {{ -ms-interpolation-mode: bicubic; border: 0; height: auto; line-height: 100%; outline: none; text-decoration: none; }}
            
            /* Main Styles */
            body {{ margin: 0; padding: 0; background-color: {THEME['bg_color']}; font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif; }}
            .container {{ width: 100%; max-width: 600px; margin: 0 auto; background-color: {THEME['bg_color']}; }}
            .card {{ background-color: {THEME['card_bg']}; border-radius: 16px; overflow: hidden; margin-bottom: 24px; box-shadow: 0 4px 6px rgba(0,0,0,0.05); }}
            .header {{ background-color: {THEME['primary']}; color: #ffffff; padding: 30px 20px; text-align: center; border-radius: 0 0 24px 24px; margin-bottom: 30px; }}
            .h1 {{ font-family: Georgia, 'Times New Roman', serif; font-size: 28px; font-weight: normal; margin: 0 0 10px 0; letter-spacing: -0.5px; }}
            .date {{ font-size: 14px; text-transform: uppercase; letter-spacing: 2px; opacity: 0.9; font-weight: bold; }}
            .article-img {{ width: 100%; height: auto; display: block; background-color: #eee; }}
            .content {{ padding: 25px; }}
            .headline {{ font-family: Georgia, 'Times New Roman', serif; font-size: 22px; color: {THEME['text_dark']}; margin: 0 0 12px 0; line-height: 1.3; text-decoration: none; display: block; font-weight: bold; }}
            .summary {{ color: {THEME['text_light']}; font-size: 16px; line-height: 1.6; margin: 0 0 20px 0; }}
            .button {{ display: inline-block; padding: 10px 20px; background-color: {THEME['primary']}; color: #ffffff; text-decoration: none; border-radius: 50px; font-weight: bold; font-size: 14px; }}
            .footer {{ text-align: center; padding: 20px; color: {THEME['text_light']}; font-size: 12px; }}
            .unsubscribe {{ color: {THEME['text_light']}; text-decoration: underline; }}
        </style>
      </head>
      <body>
        <div class="container">
            
            <div class="header">
                <div style="font-size: 40px; margin-bottom: 10px;">✨</div>
                <h1 class="h1">The Daily Bot.</h1>
                <div class="date">{today_date}</div>
            </div>
    """

    # 2. ARTICLE LOOP
    if not summaries:
        html += f"""
        <div class="card">
            <div class="content" style="text-align: center;">
                <p class="summary">No news summaries were generated today.</p>
            </div>
        </div>
        """
    else:
        for i, article in enumerate(summaries):
            title = article.get('title', 'No Title')
            summary = article.get('summary', 'No summary available.')
            url = article.get('url', '#')
            
            # Image Handling: Use Real URL or Branded Fallback
            raw_img = article.get('image_url')
            if raw_img and str(raw_img).startswith('http'):
                image_src = raw_img
            else:
                image_src = get_branded_placeholder("News Update")

            html += f"""
            <div class="card">
                <a href="{url}">
                    <img src="{image_src}" alt="Article Image" class="article-img" width="600">
                </a>
                <div class="content">
                    <a href="{url}" class="headline">{title}</a>
                    <p class="summary">{summary}</p>
                    <a href="{url}" class="button">Read on BBC &rarr;</a>
                </div>
            </div>
            """

    # 3. FOOTER
    html += f"""
            <div class="footer">
                <p>Generated by AI • Curated for You</p>
                <p>
                    <a href="https://news-summary-3baaa.web.app/unsubscribe.html" class="unsubscribe">Unsubscribe</a>
                </p>
                <p style="margin-top: 20px; opacity: 0.5;">Built by Owmyr</p>
            </div>
            
        </div> </body>
    </html>
    """
    
    return html

def send_summary_email(summaries, sender_email, sender_password, recipient_email):
    """Sends the formatted HTML email."""
    
    message = MIMEMultipart("alternative")
    today_str = datetime.date.today().strftime('%B %d')
    
    message["Subject"] = f"✨ Your Daily Briefing - {today_str}"
    message["From"] = f"The Daily Bot <{sender_email}>"
    message["To"] = recipient_email

    # Generate HTML
    html_body = format_html_body(summaries)
    
    # Attach HTML
    message.attach(MIMEText(html_body, "html"))

    # Secure Connection
    context = ssl.create_default_context()

    try:
        smtp_server = "smtp.gmail.com"
        port = 465 
        
        print("\n📧 Connecting to email server...")
        with smtplib.SMTP_SSL(smtp_server, port, context=context) as server:
            server.login(sender_email, sender_password)
            print("Login successful. Sending email...")
            server.sendmail(sender_email, recipient_email, message.as_string())
            print("✅ Email sent successfully!")
            
    except smtplib.SMTPAuthenticationError:
        print("❌ SMTP ERROR: Auth failed. Check App Password.")
    except Exception as e:
        print(f"❌ Email error: {e}")