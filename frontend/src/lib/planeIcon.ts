/** White silhouette (nose up) used as a mask: deck.gl tints it per aircraft. */
const PLANE_SVG = `<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64">
<path fill="#fff" d="M32 2c2.2 0 3.6 2.6 3.6 6.4v14.1l22.4 13.3v5.6l-22.4-7v12.3l7 5.3v4.6L32 53.4l-10.6 3.2V52l7-5.3V34.4l-22.4 7v-5.6l22.4-13.3V8.4C28.4 4.6 29.8 2 32 2z"/>
</svg>`;

export const PLANE_ATLAS = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(PLANE_SVG)}`;

export const PLANE_MAPPING = {
  plane: { x: 0, y: 0, width: 64, height: 64, anchorY: 32, mask: true },
};
