// functions/index.js

const functions = require("firebase-functions");
const admin = require("firebase-admin");
const cors = require("cors")({origin: true});

admin.initializeApp();

const db = admin.firestore();

// A simple email validation regex
const isInvalidEmail = (email) => {
  return !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
};

exports.addSubscriber = functions.https.onRequest((req, res) => {
  // Use CORS to allow requests from our web app
  cors(req, res, async () => {
    // 1. We only accept POST requests
    if (req.method !== "POST") {
      return res.status(405).json({message: "Method Not Allowed"});
    }

    const {email} = req.body;

    // 2. Validate the email format
    if (!email || isInvalidEmail(email)) {
      return res.status(400).json({message: "Please provide a valid email."});
    }

    try {
      const subscribersRef = db.collection("subscribers");

      // 3. Check if the email already exists in the database
      const snapshot = await subscribersRef.where("email", "==", email).get();
      if (!snapshot.empty) {
        return res.status(409).json({message: "This email is already subscribed."});
      }

      // 4. Add the new subscriber to the database
      await subscribersRef.add({
        email: email,
        subscribedAt: admin.firestore.FieldValue.serverTimestamp(),
      });

      // 5. Send a success response
      return res.status(201).json({message: "Subscription successful! Thank you."});

    } catch (error) {
      console.error("Error adding subscriber:", error);
      return res.status(500).json({message: "Something went wrong. Please try again."});
    }
  });
});