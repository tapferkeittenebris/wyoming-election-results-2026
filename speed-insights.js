// Vercel Speed Insights initialization
import { injectSpeedInsights } from '@vercel/speed-insights';

// Initialize Speed Insights when DOM is ready
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => {
    injectSpeedInsights();
  });
} else {
  injectSpeedInsights();
}
