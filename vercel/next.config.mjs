const config={poweredByHeader:false,output:'standalone',experimental:{},async headers(){return [{source:'/:path*',headers:[
 {key:'X-Content-Type-Options',value:'nosniff'},{key:'Referrer-Policy',value:'no-referrer'},{key:'X-Frame-Options',value:'DENY'},
 {key:'Content-Security-Policy',value:"default-src 'self'; script-src 'self' https://challenges.cloudflare.com; style-src 'self'; font-src 'self'; img-src 'self' blob:; connect-src 'self' https://challenges.cloudflare.com https://*.blob.vercel-storage.com; frame-src https://challenges.cloudflare.com; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"}
 ]}]}};
export default config;
