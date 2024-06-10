module.exports = {
    extends: '@edx/eslint-config',
    root: true,
    settings: {
        'import/resolver': 'webpack',
    },
    rules: {
        indent: ['error', 4],
        'import/extensions': 'off',
        'import/no-unresolved': 'off',
        'react/jsx-indent': 'off',
        'react/jsx-indent-props': 'off',
    },
};
