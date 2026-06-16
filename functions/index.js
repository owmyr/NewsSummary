// functions/index.js
//
// Cloud Functions for the daily-bot subscription system.
// The welcome email uses the latest rendered HTML template stored in
// Firestore by the Python daily_bot package, so the template is
// maintained in a single place (src/daily_bot/templates/email.html.j2).

const functions = require("firebase-functions");
const admin = require("firebase-admin");
const cors = require("cors")({origin: true});
const nodemailer = require("nodemailer");

admin.initializeApp();
const db = admin.firestore();

// ==========================================================
//  HTML ESCAPING (XSS protection for AI-generated content)
// ==========================================================
const escapeHtml = (str) => {
  if (str == null) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
};

// ==========================================================
//  EMAIL VALIDATION
// ==========================================================
const isInvalidEmail = (email) => {
  return !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
};

// ==========================================================
//  WELCOME EMAIL — fetch template from Firestore
// ==========================================================
const sendWelcomeEmail = async (recipientEmail) => {
  const senderEmail = process.env.SENDER_EMAIL;
  const senderPass = process.env.SENDER_PASSWORD;

  if (!senderEmail || !senderPass) {
    console.warn("Skipping welcome email: missing SENDER_EMAIL or SENDER_PASSWORD.");
    return;
  }

  const templateSnap = await db.collection("emailTemplates").doc("latest").get();
  if (!templateSnap.exists) {
    console.warn("No email template in Firestore; skipping welcome email.");
    return;
  }

  const htmlBody = (templateSnap.data() || {}).html;
  if (!htmlBody) {
    console.warn("Empty template in Firestore; skipping welcome email.");
    return;
  }

  const transporter = nodemailer.createTransport({
    service: "gmail",
    auth: {user: senderEmail, pass: senderPass},
  });

  await transporter.sendMail({
    from: `"The Daily Bot" <${senderEmail}>`,
    to: recipientEmail,
    subject: "\u2728 Welcome! Here is today's briefing",
    html: htmlBody,
  });
  console.log(`Welcome email sent to ${recipientEmail}`);
};

// ==========================================================
//  FUNCTION 1: ADD SUBSCRIBER + SEND WELCOME EMAIL
// ==========================================================
exports.addSubscriber = functions.https.onRequest((req, res) => {
  cors(req, res, async () => {
    if (req.method !== "POST") {
      return res.status(405).json({message: "Method Not Allowed"});
    }

    const {email, sources} = req.body;
    if (!email || isInvalidEmail(email)) {
      return res.status(400).json({message: "Please provide a valid email."});
    }

    const VALID_SOURCES = ["bbc", "g1"];
    let subscriberSources = ["bbc"];
    if (Array.isArray(sources) && sources.length > 0) {
      subscriberSources = sources.filter((s) => VALID_SOURCES.includes(s));
      if (subscriberSources.length === 0) subscriberSources = ["bbc"];
    }

    try {
      const subscribersRef = db.collection("subscribers");

      const snapshot = await subscribersRef.where("email", "==", email).get();
      if (!snapshot.empty) {
        return res.status(409).json({message: "This email is already subscribed."});
      }

      await subscribersRef.add({
        email: email,
        sources: subscriberSources,
        subscribedAt: admin.firestore.FieldValue.serverTimestamp(),
      });

      try {
        await sendWelcomeEmail(email);
      } catch (emailError) {
        console.error("Failed to send welcome email:", emailError);
      }

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
      if (snapshot.empty) return res.status(404).json({error: "No news summaries found"});

      const data = snapshot.docs[0].data();
      const articles = (data.articles || []).map((a) => ({
        title: escapeHtml(a.title),
        summary: escapeHtml(a.summary),
        url: a.url,
        image_url: a.image_url,
        category: escapeHtml(a.category),
        source: escapeHtml(a.source || "bbc"),
      }));

      return res.status(200).json({date: data.date, articles});
    } catch (err) {
      console.error(err);
      return res.status(500).json({error: "Server error"});
    }
  });
});

// ==========================================================
//  FUNCTION 3: UNSUBSCRIBE API
// ==========================================================
exports.unsubscribeUser = functions.https.onRequest((req, res) => {
  cors(req, res, async () => {
    if (req.method !== "POST") return res.status(405).json({message: "Method not allowed"});
    const {email} = req.body;
    if (!email || isInvalidEmail(email)) return res.status(400).json({message: "Invalid email"});

    try {
      const ref = db.collection("subscribers");
      const docs = await ref.where("email", "==", email).get();
      if (docs.empty) return res.status(404).json({message: "Email not found"});

      const batch = db.batch();
      docs.forEach((d) => batch.delete(d.ref));
      await batch.commit();

      return res.status(200).json({message: "Unsubscribed successfully"});
    } catch (err) {
      console.error(err);
      return res.status(500).json({message: "Server error"});
    }
  });
});
