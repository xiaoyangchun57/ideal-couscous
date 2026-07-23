import { useEffect, useRef } from 'react';
import { useMap } from 'react-leaflet';
import L from 'leaflet';

export function MapAutoFitter({ sites, resetKey }) {
  const map = useMap();
  const fitted = useRef(false);
  const previousResetKey = useRef(resetKey);

  useEffect(() => {
    if (!sites?.length) return;

    const validSites = sites.filter((site) => site.lat && site.lng);
    if (!validSites.length) return;

    // Preserve the operator's current view until an explicit map reset is requested.
    if (!fitted.current || previousResetKey.current !== resetKey) {
      map.fitBounds(L.latLngBounds(validSites.map((site) => [site.lat, site.lng])), {
        padding: [60, 60],
        maxZoom: 14,
      });
      fitted.current = true;
      previousResetKey.current = resetKey;
    }
  }, [map, resetKey, sites]);

  return null;
}

export function MapFlyTo({ position, zoom = 15 }) {
  const map = useMap();

  useEffect(() => {
    if (position) map.flyTo(position, zoom, { duration: 1.2 });
  }, [map, position, zoom]);

  return null;
}
