/**
 * Resolves a phone-scannable URL: public HTTPS first, then LAN auto-discovery.
 * Sets window.__GEENGREEN_URL__ for Flutter to read.
 */
(function () {
  function pickPrivateIp(candidate) {
    var m = /(\d{1,3}(?:\.\d{1,3}){3})/.exec(candidate || '');
    if (!m) return null;
    var ip = m[1];
    if (/^(192\.168\.|10\.|172\.(1[6-9]|2\d|3[01])\.)/.test(ip)) return ip;
    return null;
  }

  function discoverLanIp(timeoutMs) {
    return new Promise(function (resolve) {
      var found = null;
      try {
        var pc = new RTCPeerConnection({ iceServers: [{ urls: 'stun:stun.l.google.com:19302' }] });
        pc.createDataChannel('geengreen');
        pc.onicecandidate = function (e) {
          if (!e || !e.candidate) return;
          var ip = pickPrivateIp(e.candidate.candidate);
          if (ip) {
            found = ip;
            pc.close();
            resolve(ip);
          }
        };
        pc.createOffer().then(function (o) { return pc.setLocalDescription(o); });
        setTimeout(function () {
          try { pc.close(); } catch (_) {}
          resolve(found);
        }, timeoutMs || 2800);
      } catch (_) {
        resolve(null);
      }
    });
  }

  async function resolveMobileUrl(opts) {
    opts = opts || {};
    var publicUrl = (opts.publicUrl || '').trim();
    var port = String(opts.port || location.port || '8086');
    var forceLan = !!opts.forceLan;

    if (publicUrl && !forceLan) {
      window.__GEENGREEN_URL__ = publicUrl.replace(/\/?$/, '/');
      return window.__GEENGREEN_URL__;
    }

    var host = location.hostname;
    if (host && host !== 'localhost' && host !== '127.0.0.1') {
      window.__GEENGREEN_URL__ = location.protocol + '//' + location.host + '/';
      return window.__GEENGREEN_URL__;
    }

    var saved = localStorage.getItem('geengreen_lan_host');
    if (saved) {
      window.__GEENGREEN_URL__ = 'http://' + saved + ':' + port + '/';
      return window.__GEENGREEN_URL__;
    }

    var ip = await discoverLanIp(2800);
    if (ip) {
      localStorage.setItem('geengreen_lan_host', ip);
      window.__GEENGREEN_URL__ = 'http://' + ip + ':' + port + '/';
      return window.__GEENGREEN_URL__;
    }

    window.__GEENGREEN_URL__ = null;
    return null;
  }

  window.geengreenResolveMobileUrl = resolveMobileUrl;
  window.geengreenMobileUrl = function () { return window.__GEENGREEN_URL__ || null; };

  // Prefetch on every page load (Flutter reads __GEENGREEN_URL__).
  var meta = document.querySelector('meta[name="geengreen-public-url"]');
  var publicFromMeta = meta ? meta.getAttribute('content') : '';
  resolveMobileUrl({
    publicUrl: publicFromMeta,
    port: location.port || '8086',
    forceLan: location.search.indexOf('lan=1') >= 0,
  });
})();
