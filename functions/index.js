// functions/index.js

const functions = require("firebase-functions");
const admin = require("firebase-admin");
const cors = require("cors")({origin: true});
const nodemailer = require("nodemailer"); // <--- NEW IMPORT

admin.initializeApp();
const db = admin.firestore();

// ==========================================================
//  THEME & EMAIL TEMPLATE (Ported to JavaScript)
// ==========================================================
const THEME = {
  bg_color: "#faf9f6",
  card_bg: "#ffffff",
  primary: "#709775",
  text_dark: "#292524",
  text_light: "#78716c",
};

// Generates the HTML for the email
const generateEmailHtml = (summaries) => {
  const todayDate = new Date().toLocaleDateString("en-US", {
    weekday: "long",
    month: "long",
    day: "numeric",
  });

  // Header & CSS
  let html = `
    <!DOCTYPE html>
    <html>
      <head>
        <style>
            body { margin: 0; padding: 0; background-color: ${THEME.bg_color}; font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif; }
            .container { width: 100%; max-width: 600px; margin: 0 auto; background-color: ${THEME.bg_color}; }
            .card { background-color: ${THEME.card_bg}; border-radius: 16px; overflow: hidden; margin-bottom: 24px; box-shadow: 0 4px 6px rgba(0,0,0,0.05); }
            .header { background-color: ${THEME.primary}; color: #ffffff; padding: 30px 20px; text-align: center; border-radius: 0 0 24px 24px; margin-bottom: 30px; }
            .h1 { font-family: Georgia, serif; font-size: 28px; margin: 0 0 10px 0; }
            .date { font-size: 14px; text-transform: uppercase; letter-spacing: 2px; opacity: 0.9; font-weight: bold; }
            .article-img { width: 100%; height: auto; display: block; background-color: #eee; }
            .content { padding: 25px; }
            .headline { font-family: Georgia, serif; font-size: 22px; color: ${THEME.text_dark}; margin: 0 0 12px 0; text-decoration: none; display: block; font-weight: bold; }
            .summary { color: ${THEME.text_light}; font-size: 16px; line-height: 1.6; margin: 0 0 20px 0; }
            .button { display: inline-block; padding: 10px 20px; background-color: ${THEME.primary}; color: #ffffff; text-decoration: none; border-radius: 50px; font-weight: bold; font-size: 14px; }
            .footer { text-align: center; padding: 20px; color: ${THEME.text_light}; font-size: 12px; }
            .unsubscribe { color: ${THEME.text_light}; text-decoration: underline; }
        </style>
      </head>
      <body>
        <div class="container">
            <div class="header">
                <div style="font-size: 40px; margin-bottom: 10px;">✨</div>
                <h1 class="h1">The Daily Bot.</h1>
                <div class="date">${todayDate}</div>
            </div>
  `;

  // Article Loop
  if (!summaries || summaries.length === 0) {
    html += `<div class="card"><div class="content"><p class="summary">No news available today.</p></div></div>`;
  } else {
    summaries.forEach((article) => {
      const title = article.title || "No Title";
      const summary = article.summary || "No summary available.";
      const url = article.url || "#";
      
      // Image Logic
      let imageSrc = `https://placehold.co/600x300/F0F4F1/709775?text=${encodeURIComponent("News Update")}`;
      if (article.image_url && article.image_url.startsWith("http")) {
        imageSrc = article.image_url;
      }

      html += `
        <div class="card">
            <a href="${url}">
                <img src="${imageSrc}" alt="Article Image" class="article-img" width="600">
            </a>
            <div class="content">
                <a href="${url}" class="headline">${title}</a>
                <p class="summary">${summary}</p>
                <a href="${url}" class="button">Read on BBC &rarr;</a>
            </div>
        </div>
      `;
    });
  }

  // Footer
  html += `
            <div class="footer">
                <p>Welcome to the club! Here is today's briefing.</p>
                <p><a href="https://daily-bbc-bot.web.app/unsubscribe.html" class="unsubscribe">Unsubscribe</a></p>
            </div>
        </div>
      </body>
    </html>
  `;

  return html;
};


// ==========================================================
//  HELPER: EMAIL VALIDATION
// ==========================================================
const isInvalidEmail = (email) => {
  return !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
};

// ==========================================================
//  FUNCTION 1: ADD SUBSCRIBER + SEND WELCOME EMAIL
// ==========================================================
exports.addSubscriber = functions.https.onRequest((req, res) => {
  cors(req, res, async () => {
    if (req.method !== "POST") {
      return res.status(405).json({message: "Method Not Allowed"});
    }

    const {email} = req.body;

    if (!email || isInvalidEmail(email)) {
      return res.status(400).json({message: "Please provide a valid email."});
    }

    try {
      const subscribersRef = db.collection("subscribers");

      // Check duplicates
      const snapshot = await subscribersRef.where("email", "==", email).get();
      if (!snapshot.empty) {
        return res.status(409).json({message: "This email is already subscribed."});
      }

      // Add to DB
      await subscribersRef.add({
        email: email,
        subscribedAt: admin.firestore.FieldValue.serverTimestamp(),
      });

      // --- NEW: SEND WELCOME EMAIL LOGIC ---
      try {
        // 1. Get credentials from Environment Variables
        const senderEmail = process.env.SENDER_EMAIL; 
        const senderPass = process.env.SENDER_PASSWORD;

        if (senderEmail && senderPass) {
          // 2. Fetch latest news
          const newsSnap = await db.collection("dailySummaries")
            .orderBy("date", "desc")
            .limit(1)
            .get();

          if (!newsSnap.empty) {
            const articles = newsSnap.docs[0].data().articles;
            const htmlBody = generateEmailHtml(articles);

            // 3. Setup Transporter
            const transporter = nodemailer.createTransport({
              service: "gmail",
              auth: { user: senderEmail, pass: senderPass },
            });

            // 4. Send
            await transporter.sendMail({
              from: `"The Daily Bot" <${senderEmail}>`,
              to: email, // Only the new subscriber
              subject: "✨ Welcome! Here is today's briefing",
              html: htmlBody,
            });
            console.log(`Welcome email sent to ${email}`);
          }
        } else {
            console.warn("Skipping welcome email: Missing SENDER_EMAIL or SENDER_PASSWORD env vars.");
        }
      } catch (emailError) {
        console.error("Failed to send welcome email:", emailError);
        // We do NOT return an error to the user, because subscription was successful.
      }
      // -------------------------------------

      return res.status(201).json({message: "Subscription successful! Check your inbox."});

    } catch (error) {
      console.error("Error adding subscriber:", error);
      return res.status(500).json({message: "Something went wrong. Please try again."});
    }
  });
});

// ==========================================================
//  FUNCTION 2: LATEST NEWS API
// ==========================================================
exports.latestNews = functions.https.onRequest((req, res) => {
  cors(req, res, async () => {
    try {
      const snapshot = await db.collection("dailySummaries").orderBy("date", "desc").limit(1).get();
      if (snapshot.empty) return res.status(404).json({ error: "No news summaries found" });
      return res.status(200).json(snapshot.docs[0].data());
    } catch (err) {
      console.error(err);
      return res.status(500).json({ error: "Server error" });
    }
  });
});

// ==========================================================
//  FUNCTION 3: UNSUBSCRIBE API
// ==========================================================
exports.unsubscribeUser = functions.https.onRequest((req, res) => {
  cors(req, res, async () => {
    if (req.method !== "POST") return res.status(405).json({ message: "Method not allowed" });
    const { email } = req.body;
    if (!email || isInvalidEmail(email)) return res.status(400).json({ message: "Invalid email" });

    try {
      const ref = db.collection("subscribers");
      const docs = await ref.where("email", "==", email).get();
      if (docs.empty) return res.status(404).json({ message: "Email not found" });

      const batch = db.batch();
      docs.forEach((d) => batch.delete(d.ref));
      await batch.commit();

      return res.status(200).json({ message: "Unsubscribed successfully" });
    } catch (err) {
      console.error(err);
      return res.status(500).json({ message: "Server error" });
    }
  });
});