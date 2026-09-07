/**
 * Google Apps Script Universal Webhook Relay for Antigravity & Web-Gemini
 * 
 * 🔒 SECURITY NOTICE:
 * This script contains ZERO hardcoded secrets, tokens, or passwords.
 * All sensitive values are stored securely in Google Apps Script "Script Properties".
 * 
 * SETUP INSTRUCTIONS:
 * 1. Open https://script.google.com/ and create a "New project".
 * 2. Paste this code into Code.gs and save.
 * 3. Go to "Project Settings" (gear icon on the left) -> "Script Properties".
 * 4. Add the following Script Properties:
 *    - AGY_ENDPOINT_BASE : https://levra7-ai.mooo.com/api/agy
 *    - AGY_SECRET_TOKEN  : <your_secret_token_from_webhook_secret.txt>
 * 5. Click "Deploy" -> "New deployment" -> Select type "Web app".
 *    - Description: "AGY Secure Relay"
 *    - Execute as: "Me"
 *    - Who has access: "Anyone" (or "Anyone with Google Account")
 * 6. Copy the generated Web App URL:
 *    https://script.google.com/macros/s/AKfycb.../exec
 */

function getConfiguration() {
  var props = PropertiesService.getScriptProperties();
  return {
    endpointBase: props.getProperty('AGY_ENDPOINT_BASE') || "https://levra7-ai.mooo.com/api/agy",
    secretToken: props.getProperty('AGY_SECRET_TOKEN') || ""
  };
}

/**
 * Handles incoming GET requests from Web-Gemini or browser.
 * Supports: ?action=exec, ?action=vitals, ?action=read, ?action=search, ?action=ls, ?action=profile
 */
function doGet(e) {
  var config = getConfiguration();
  var params = (e && e.parameter) || {};
  
  // Resolve authentication token
  var token = params.token || config.secretToken;
  if (!token) {
    return ContentService.createTextOutput(JSON.stringify({
      status: "error",
      error: "Unauthorized: Missing AGY_SECRET_TOKEN in Script Properties or request parameter."
    }, null, 2)).setMimeType(ContentService.MimeType.JSON);
  }

  // Determine destination endpoint action
  var action = (params.action || (params.path && !params.path.includes("/") ? params.path : "exec")).replace(/^\/+/, "");
  var targetUrl = config.endpointBase.replace(/\/+$/, "") + "/" + action;

  // Build query parameters
  var queryParts = [];
  queryParts.push("token=" + encodeURIComponent(token));
  for (var key in params) {
    if (key !== "action" && key !== "token") {
      // If path was used as action name, don't forward it; otherwise forward path parameter
      if (key === "path" && action === params.path) {
        continue;
      }
      queryParts.push(encodeURIComponent(key) + "=" + encodeURIComponent(params[key]));
    }
  }

  if (queryParts.length > 0) {
    targetUrl += "?" + queryParts.join("&");
  }

  var options = {
    method: "get",
    headers: {
      "Authorization": "Bearer " + token,
      "User-Agent": "GoogleAppsScript-AGY-Relay"
    },
    muteHttpExceptions: true
  };

  var t0 = new Date().getTime();
  try {
    var response = UrlFetchApp.fetch(targetUrl, options);
    var t1 = new Date().getTime();
    var code = response.getResponseCode();
    var content = response.getContentText();
    var fmt = params.format || "json";

    if (fmt === "text") {
      return ContentService.createTextOutput(content).setMimeType(ContentService.MimeType.TEXT);
    }

    try {
      var jsonOutput = JSON.parse(content);
      jsonOutput.relay_duration_ms = (t1 - t0);
      return ContentService.createTextOutput(JSON.stringify(jsonOutput, null, 2))
        .setMimeType(ContentService.MimeType.JSON);
    } catch (parseErr) {
      return ContentService.createTextOutput(content)
        .setMimeType(ContentService.MimeType.TEXT);
    }
  } catch (err) {
    return ContentService.createTextOutput(JSON.stringify({
      status: "error",
      error: "Relay request failed: " + err.toString()
    }, null, 2)).setMimeType(ContentService.MimeType.JSON);
  }
}

/**
 * Handles incoming POST requests from Web-Gemini.
 * Supports: exec, write, subagent/spawn, vitals, etc.
 */
function doPost(e) {
  var config = getConfiguration();
  var params = (e && e.parameter) || {};
  var body = {};

  try {
    if (e && e.postData && e.postData.contents) {
      body = JSON.parse(e.postData.contents);
    }
  } catch (err) {
    body = params;
  }

  var token = body.token || params.token || config.secretToken;
  if (!token) {
    return ContentService.createTextOutput(JSON.stringify({
      status: "error",
      error: "Unauthorized: Missing AGY_SECRET_TOKEN in Script Properties or request body."
    }, null, 2)).setMimeType(ContentService.MimeType.JSON);
  }

  var action = (body.action || params.action || params.path || "exec").replace(/^\/+/, "");
  var targetUrl = config.endpointBase.replace(/\/+$/, "") + "/" + action;

  // Inject token in payload
  body.token = token;

  var options = {
    method: "post",
    contentType: "application/json; charset=utf-8",
    payload: JSON.stringify(body),
    headers: {
      "Authorization": "Bearer " + token,
      "User-Agent": "GoogleAppsScript-AGY-Relay"
    },
    muteHttpExceptions: true
  };

  var t0 = new Date().getTime();
  try {
    var response = UrlFetchApp.fetch(targetUrl, options);
    var t1 = new Date().getTime();
    var content = response.getContentText();

    try {
      var jsonOutput = JSON.parse(content);
      jsonOutput.relay_duration_ms = (t1 - t0);
      return ContentService.createTextOutput(JSON.stringify(jsonOutput, null, 2))
        .setMimeType(ContentService.MimeType.JSON);
    } catch (parseErr) {
      return ContentService.createTextOutput(content)
        .setMimeType(ContentService.MimeType.TEXT);
    }
  } catch (err) {
    return ContentService.createTextOutput(JSON.stringify({
      status: "error",
      error: "Relay POST request failed: " + err.toString()
    }, null, 2)).setMimeType(ContentService.MimeType.JSON);
  }
}
