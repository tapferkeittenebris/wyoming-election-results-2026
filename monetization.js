window.MONETIZATION = {
  // Add your approved Google AdSense publisher ID here, e.g. ca-pub-1234567890123456.
  // Leave blank until Google approves the site. Auto Ads will load automatically once set.
  adsenseClient: "",

  // Commercial sponsorships only by default. Candidate/PAC/political advertising should be reviewed separately.
  sponsors: {
    presenting: null,
    houseMap: null,
    senateMap: null,
    supporting: []
  }
};

(function () {
  const cfg = window.MONETIZATION || {};

  if (cfg.adsenseClient && /^ca-pub-\d+$/.test(cfg.adsenseClient)) {
    const s = document.createElement('script');
    s.async = true;
    s.crossOrigin = 'anonymous';
    s.src = 'https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=' + encodeURIComponent(cfg.adsenseClient);
    document.head.appendChild(s);
  }

  function sponsorHtml(s, fallback) {
    if (!s) return fallback;
    const name = String(s.name || 'Sponsor');
    const url = String(s.url || '#');
    const logo = s.logo ? '<img src="' + String(s.logo) + '" alt="' + name.replace(/"/g, '&quot;') + '">' : '';
    return '<a class="sponsor-live" href="' + url + '" target="_blank" rel="sponsored noopener">' + logo + '<span><small>Sponsored by</small><strong>' + name + '</strong></span></a>';
  }

  const presenting = document.getElementById('presentingSponsor');
  if (presenting) presenting.innerHTML = sponsorHtml(cfg.sponsors && cfg.sponsors.presenting, presenting.innerHTML);

  const hm = document.getElementById('houseMapSponsor');
  if (hm) hm.innerHTML = sponsorHtml(cfg.sponsors && cfg.sponsors.houseMap, hm.innerHTML);

  const sm = document.getElementById('senateMapSponsor');
  if (sm) sm.innerHTML = sponsorHtml(cfg.sponsors && cfg.sponsors.senateMap, sm.innerHTML);

  const supporting = document.getElementById('supportingSponsors');
  if (supporting && cfg.sponsors && Array.isArray(cfg.sponsors.supporting) && cfg.sponsors.supporting.length) {
    supporting.innerHTML = cfg.sponsors.supporting.map(s => sponsorHtml(s, '')).join('');
  }
})();
