/** @type {import('tailwindcss').Config} */
module.exports = {
    content: [
        // This is the key change. The './**/' pattern will search
        // through ALL directories in your project for a `templates` folder.
        "./**/templates/**/*.html",
    ],
    theme: {
        extend: {},
    },
    plugins: [],
};
