import { upload } from '@vercel/blob/client';

window.leafBlobUpload = (pathname, file, options) => upload(pathname, file, options);
