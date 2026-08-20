// Vercel Speed Insights
// Dynamically injects the Speed Insights tracking script
// See: https://vercel.com/docs/speed-insights/quickstart

import { injectSpeedInsights } from '@vercel/speed-insights';

// Initialize Speed Insights with default configuration
injectSpeedInsights({
  debug: false, // Set to true for debug logging in development
  // Additional options can be configured here:
  // sampleRate: 1, // 1 = 100% of events tracked
  // beforeSend: (event) => event, // Middleware to modify events before sending
});
