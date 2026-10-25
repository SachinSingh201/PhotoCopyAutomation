# UPI Payment Verification & Settlement Architecture

## 1. Executive Summary

This document defines the production architecture for verifying customer UPI payments in the **PhotocopyAutomation** and **YouPI-PG** ecosystem. It details:
1. Multi-merchant app normalization across India's top 4 business payment providers.
2. The multi-layer "Zero Money Lost" guarantee for customers.
3. Complete **Zero-APK** architectural options that eliminate the need to install any custom or sideloaded APK on the merchant's or shopkeeper's phone.

---

## 2. Multi-Merchant Business Apps Normalization

In commercial photocopy and print shops, shopkeepers display a single counter standee QR code from one of the major merchant payment providers. Due to NPCI UPI interoperability, customers can scan that single QR code using **any** consumer UPI app (Google Pay, PhonePe, Paytm, BHIM, CRED, Navi, etc.).

When the payment clears, the merchant business application installed on the counter phone receives an instant cloud push notification:

| Provider | Android Package Name | Expected Notification Format | Key Data Captured |
| :--- | :--- | :--- | :--- |
| **Google Pay for Business** | `com.google.android.apps.nbu.paisa.merchant` | `₹20.00 received from Rahul Kumar. Ref No: 429810123456` | Amount (`₹20.00`), 12-digit UTR (`429810123456`) |
| **PhonePe Business** | `com.phonepe.app.business` | `Payment of ₹20.00 received from Rahul. Txn ID: T... UTR: 429810123456` | Amount (`₹20.00`), 12-digit UTR (`429810123456`) |
| **Paytm for Business** | `com.paytm.business` | `Received ₹20 from Rahul (UPI Ref: 429810123456)` | Amount (`₹20.00`), 12-digit UTR (`429810123456`) |
| **BharatPe for Merchants** | `com.bharatpe.app` | `₹20 received via UPI. Ref: 429810123456` | Amount (`₹20.00`), 12-digit UTR (`429810123456`) |

### Ingest Normalization Specification
Regardless of which merchant provider sends the notification, the ingestion layer normalizes the payload into a single canonical contract dispatched to YouPI-PG (`POST /api/v1/pollers/android/events`):

```json
{
  "deviceId": "DEVICE_UUID",
  "amount": 20.00,
  "amountPaise": "2000",
  "bankReferenceNumber": "429810123456",
  "observedAt": "2026-10-25T15:45:00.000Z",
  "rawNotification": {
    "title": "Payment Received",
    "text": "Exact notification body",
    "package": "com.phonepe.app.business"
  }
}
```

---

## 3. The "Zero Money Lost" Guarantee: 4-Layer Safety Net

Automated payment verification must handle real-world failures (network dropouts, battery exhaustion, concurrent identical amounts, delayed payments) without customer funds ever being lost.

```
                              CUSTOMER PAYS VIA UPI
                                        │
           ┌────────────────────────────┴────────────────────────────┐
           ▼                                                         ▼
 [Layer 1: Automatic Notification]                        [Layer 3: WhatsApp Customer Fallback]
 Merchant app posts push alert.                           Customer sees 12-digit UTR on screen.
 Amount & UTR parsed & sent to YouPI-PG.                  Customer replies: "PAID 429810123456"
           │                                                         │
           └────────────────────────────┬────────────────────────────┘
                                        ▼
                            Order Verified as PAID
                                        │
                         [Layer 2: Concurrency & Recovery]
                         • FIFO queue prevents slot collisions.
                         • 30-min late recovery window saves expired orders.
                                        │
                        (If phone was dead / offline)
                                        ▼
                         [Layer 4: Counter Token Fail-Safe]
                         Customer shows Token "TK-104" at counter.
                         Shopkeeper one-click approves in Admin Dashboard.
```

### Layer Details:

### Layer 1: Mathematical Attribution via Unique 12-Digit UTR
Every UPI transaction across India generates a globally unique 12-digit numeric reference (the UTR / RRN) issued by NPCI.
- Even if two customers pay the exact same amount (e.g. ₹10.00) at the exact same second, their UTRs are completely distinct.
- YouPI-PG enforces a database unique constraint (`idx_observations_unique_attributed_bank_ref`). A UTR cannot be attributed twice or stolen by another session.

### Layer 2: Concurrency Arbiter & 30-Minute Late Recovery Window
- **FIFO Slot Arbiter**: Exactly one payment session occupies an active listening slot per VPA at any instant, preventing amount ambiguity.
- **Late Payment Recovery**: If a customer takes 6 minutes to pay (after the 5-minute checkout countdown expires), YouPI-PG does **not** discard the money. The 30-minute late attribution engine detects the credit, marks the order `SUCCESS`, and triggers the print queue automatically.

### Layer 3: WhatsApp Customer UTR Self-Verification (`PAID <UTR>`)
- If the phone notification is delayed due to network throttling, the customer is empowered to self-verify.
- When an order is awaiting payment, the WhatsApp bot prompts:
  > *"If you paid via UPI, reply with your 12-digit UTR: `PAID <12-digit UTR>`"*
- When the customer submits their UTR, PhotocopyAutomation verifies it against the payment database and immediately transitions the order to `PAID` ➔ `QUEUED` ➔ `PRINTING`.

### Layer 4: Pickup Token & Shopkeeper Cashier Till (Hardware Fail-Safe)
- If the merchant phone battery completely dies or the phone is lost, the customer still has their **Pickup Token** (e.g., `TK-4819`) and Order Code (`#ORD-9021`) on WhatsApp.
- The customer presents `TK-4819` at the counter.
- The shopkeeper opens the web admin dashboard on the shop PC and clicks **"Verify Payment & Print"**. The document spools to the printer immediately.

---

## 4. Addressing the Zero-APK Requirement

### The Core Principle
To capture status-bar notifications popping up on a physical Android phone **passively and automatically**, some software on that physical device must be authorized by Android OS to read notifications.

**HOWEVER, you are NOT required to install any custom or sideloaded APK.**

If you do not want to install any APK on your phone, there are **three robust, production-grade Zero-APK alternatives**:

---

### Zero-APK Alternative 1: Official Cloud Payment Gateway (Razorpay / Cashfree)
*Recommended for commercial production setups with zero phone dependencies.*

- **How it works**:
  1. PhotocopyAutomation generates a dynamic UPI payment link or QR code via **Razorpay** or **Cashfree**.
  2. The customer scans and pays.
  3. The payment gateway verifies the transaction server-to-server and sends an HTTP POST **webhook** directly to your backend (`/api/v1/payments/razorpay/webhook`).
- **Phone Requirements**: **Zero.** No phone app, no APK, no phone even needs to be on.
- **Reliability**: 99.99% cloud uptime; bank-grade cryptographic signature verification (`X-Razorpay-Signature`).
- **Status in Codebase**: Already implemented in `backend/services/payments/razorpay.py`.

---

### Zero-APK Alternative 2: Server-Side Bank Email Observer (`apps/gmail-observer`)
*Recommended for zero-fee direct bank settlements without any phone app.*

- **How it works**:
  1. Indian commercial banks (HDFC, ICICI, SBI, Axis, Kotak) send an instant email notification within 1–3 seconds of any UPI credit.
  2. The email contains the exact amount, account number, and 12-digit UTR.
  3. YouPI-PG's built-in **`apps/gmail-observer`** runs entirely on your shop computer/server.
  4. It monitors the bank alerts via Google Cloud OAuth2 or standard secure email polling.
- **Phone Requirements**: **Zero.** No APK on any phone.
- **Reliability**: Immune to telecom SMS delays; operates over standard internet on the host PC.

---

### Zero-APK Alternative 3: Counter Soundbox + WhatsApp UTR / Token Workflow
*Recommended for physical shop counters with an existing merchant soundbox.*

- **How it works**:
  1. Customer scans the shop's standard counter standee QR code (Paytm / PhonePe / GPay).
  2. The merchant's existing soundbox announces the payment: *"Paytm par 20 rupaye prapt hue"*.
  3. The customer sends their 12-digit UTR on WhatsApp (`PAID 429810123456`) or displays their **Pickup Token** (`TK-104`).
  4. The shopkeeper confirms on the local dashboard or the system verifies the UTR.
- **Phone Requirements**: **Zero APKs.** Uses the existing commercial soundbox hardware already on the counter.

---

### Zero-APK Alternative 4: Verified Google Play Store Automation (No Sideloading)
*If you want phone notification forwarding without installing any custom or unverified APK.*

- If phone notification forwarding is desired without sideloading an unknown APK:
  - Install a globally recognized, Play-Store-verified automation utility such as **MacroDroid** or **Tasker** (10M+ downloads, verified safe by Google Play Protect).
  - Create a simple 2-step rule inside MacroDroid:
    - **Trigger**: Notification received from selected Merchant App (Google Pay Business / PhonePe Business).
    - **Action**: HTTP POST notification text to `http://<your-server-ip>:6100/api/v1/pollers/android/events`.
  - **Result**: Zero sideloaded APKs, zero threat warnings from Play Protect, and full user transparency over every permission.

---

## 5. Architectural Decision Matrix

| Dimension | Option 1: Razorpay / Cashfree | Option 2: Gmail Observer | Option 3: Soundbox + UTR / Token | Option 4: MacroDroid (Play Store) |
| :--- | :--- | :--- | :--- | :--- |
| **Custom APK on Phone?** | ❌ **No (Zero App)** | ❌ **No (Zero App)** | ❌ **No (Zero App)** | ❌ **No (Official Play Store App)** |
| **Sideloading Security Threat?** | ❌ None | ❌ None | ❌ None | ❌ None (Google Play Verified) |
| **Transaction Fees** | 0% (UPI) / nominal PG fee | 0% (Direct bank transfer) | 0% (Direct merchant QR) | 0% (Direct merchant QR) |
| **Hardware Required** | Internet only | Shop PC + Email | Counter soundbox / PC | Android Counter Phone |
| **Verification Speed** | Real-time (1–2s) | Real-time (2–4s) | Real-time audio + manual/UTR | Real-time (1–2s) |
| **Customer Safety** | 100% Cryptographic | 100% Bank Ref match | 100% Token / UTR match | 100% UTR match |

---

## 6. Direct Merchant Webhooks: Can a Merchant Account Notify Directly Without Any Phone or App?

### The Clear Distinction

#### A. When YES (Direct Cloud-to-Server Webhook):
If you use a **Payment Gateway** (Razorpay, Cashfree, PayU) or a **Developer-Enabled Merchant Account** (like Paytm for Business):
1. You log in to the merchant portal on your computer.
2. In **Settings ➔ Webhooks / Developer API**, you enter your server's endpoint:
   `https://your-server.com/api/v1/payments/webhook`
3. When any customer scans the dynamic QR and pays, **the payment company's cloud server calls your server directly**.
4. **Phone Requirements: ABSOLUTELY ZERO.** Your phone can be turned off, out of battery, or left at home. The server receives the webhook directly from the cloud.

#### B. When NO (Physical Counter Paper Standees):
If you use a **basic free paper QR standee** from Google Pay for Business, PhonePe Business, or BharatPe:
1. Google and PhonePe do **not** provide free, self-serve webhook URLs to small individual retail merchants for offline paper standees.
2. Their architecture is designed to notify the shopkeeper through **two physical outputs**:
   - The **Soundbox speaker** on the counter (*"PhonePe par bees rupaye prapt hue"*).
   - A **Push Notification** sent to the merchant's Android phone app.
3. Therefore, to automate a paper standee without a payment gateway API, the server must either:
   - Receive the notification forwarded from the phone (via MacroDroid), **OR**
   - Read the bank's credit email alert on the PC (via Gmail Observer), **OR**
   - Have the customer provide their UTR / Pickup Token on WhatsApp.

---

## 7. Step-by-Step Setup Guide: MacroDroid (Zero Sideloading, Play Store Verified)

If you choose to use your counter phone to forward notifications, here is the exact guide to configure **MacroDroid** from Google Play Store with zero coding and zero sideloading.

### Step 1: Install MacroDroid from Google Play Store
1. Open **Google Play Store** on the counter phone.
2. Search for **MacroDroid - Device Automation** (by ArloSoft, 10M+ downloads, 4.5★).
3. Tap **Install**. *(This is an official Play Store app verified by Google Play Protect; it will never display security or data breach warnings).*

### Step 2: Grant System Permissions
1. Open MacroDroid.
2. Follow the on-screen prompt to enable **Notification Access**:
   - Go to phone `Settings ➔ Apps ➔ Special App Access ➔ Notification Access`.
   - Toggle **MacroDroid** to **ON / Allowed**.
3. **Disable Battery Optimization** for MacroDroid:
   - Go to phone `Settings ➔ Battery ➔ Battery Optimization ➔ MacroDroid ➔ Don't Optimize / Unrestricted`.
   *(This ensures Android does not put MacroDroid to sleep when the screen is off).*

### Step 3: Create the Payment Forwarding Macro
In MacroDroid, tap **Add Macro** (the large `+` icon):

#### 1. Macro Name:
Enter: `YouPI Payment Observer`

#### 2. Trigger Configuration (Red `+` button):
1. Tap the **`+`** icon on the red **Triggers** bar.
2. Select **Device Events ➔ Notification ➔ Notification Received**.
3. Select **Select Application(s)** ➔ tap **OK**.
4. Check the box for your payment app(s):
   - **PhonePe** (`com.phonepe.app`)
   - **PhonePe Business** (`com.phonepe.app.business`)
   - **Google Pay for Business** (`com.google.android.apps.nbu.paisa.merchant`)
   - **Paytm** / **Paytm for Business** (`net.one97.paytm` / `com.paytm.business`)
   - *(Optional)* **IPPB** / **Google Messages**
5. Tap **OK**.
6. Under **Text Content**, choose **Contains** and enter:
   `received`
   *(Optional secondary check: `credited`)*. Tap **OK**.

#### 3. Action Configuration (Blue `+` button):
1. Tap the **`+`** icon on the blue **Actions** bar.
2. Select **Applications ➔ Open Website / HTTP GET/POST ➔ HTTP Request**.
3. Configure the HTTP Request parameters:
   - **Request Method**: `POST`
   - **URL**: `http://<YOUR_PC_IP_ADDRESS>:6100/api/v1/pollers/android/events`  
     *(Replace `<YOUR_PC_IP_ADDRESS>` with your shop PC's local IP address, e.g., `http://192.168.1.15:6100/api/v1/pollers/android/events`, or your public tunnel domain)*.
   - **Content Type**: `application/json`
   - **Request Headers**:
     Add header:
     - Header Name: `Content-Type`
     - Header Value: `application/json`
   - **Request Body (Raw text)**:
     Enter the following JSON payload using MacroDroid magic text variables:
     ```json
     {
       "amount": "{not_text}",
       "rawNotification": {
         "title": "{not_title}",
         "text": "{not_text}",
         "package": "{not_app_package}"
       }
     }
     ```
4. Tap **OK** to save the action.

### Step 4: Save & Test the Macro
1. Tap the checkmark icon in the bottom right corner to **Save** the macro.
2. Ensure the main MacroDroid toggle switch on the home screen is set to **ON**.
3. **Test the Macro**:
   - Tap the three dots (`⋮`) on the top right of your newly created macro ➔ tap **Test Actions**.
   - Check your YouPI-PG or PhotocopyAutomation terminal logs on your PC. You will immediately see the incoming test HTTP payload received!
4. **Live Verification**:
   - Send a ₹1 or ₹5 test transfer to your UPI QR.
   - The payment app receives the credit alert.
   - MacroDroid intercepts the notification within 0.5 seconds and forwards it to the PC.
   - The PhotocopyAutomation print queue confirms payment and sends prints to the printer!

