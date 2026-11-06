<?php
/**
 * Plugin Name: Guardian Connector
 * Description: Read-only, least-privilege status endpoint for the Guardian monitoring agent. Adds a "Guardian Monitor" role that can read site health data but cannot edit content, settings, plugins or themes.
 * Version: 0.1.0
 * Requires at least: 5.6
 * Requires PHP: 7.4
 * License: GPL-2.0-or-later
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

const GUARDIAN_CONNECTOR_VERSION = '0.1.0';
const GUARDIAN_CAP                = 'guardian_read';
const GUARDIAN_ROLE               = 'guardian_monitor';

/**
 * Role: only 'read' (needed to log in / use Application Passwords) and
 * 'guardian_read' (this plugin's status endpoint). Nothing else.
 */
function guardian_connector_activate() {
	add_role( GUARDIAN_ROLE, 'Guardian Monitor', array( 'read' => true, GUARDIAN_CAP => true ) );
	$admin = get_role( 'administrator' );
	if ( $admin ) {
		$admin->add_cap( GUARDIAN_CAP );
	}
}
register_activation_hook( __FILE__, 'guardian_connector_activate' );

function guardian_connector_deactivate() {
	remove_role( GUARDIAN_ROLE );
	$admin = get_role( 'administrator' );
	if ( $admin ) {
		$admin->remove_cap( GUARDIAN_CAP );
	}
}
register_deactivation_hook( __FILE__, 'guardian_connector_deactivate' );

add_action(
	'rest_api_init',
	function () {
		register_rest_route(
			'guardian/v1',
			'/status',
			array(
				'methods'             => 'GET',
				'callback'            => 'guardian_connector_status',
				'permission_callback' => function () {
					return current_user_can( GUARDIAN_CAP );
				},
				'args'                => array(
					'seo' => array(
						'type'    => 'boolean',
						'default' => false,
					),
				),
			)
		);
	}
);

/**
 * Remove values whose key suggests a secret.
 */
function guardian_connector_redact( $value ) {
	if ( ! is_array( $value ) ) {
		return $value;
	}
	$out = array();
	foreach ( $value as $key => $item ) {
		if ( is_string( $key ) && preg_match( '/key|token|secret|password|auth/i', $key ) ) {
			$out[ $key ] = '[redacted]';
			continue;
		}
		$out[ $key ] = guardian_connector_redact( $item );
	}
	return $out;
}

function guardian_connector_updates() {
	if ( ! function_exists( 'get_plugins' ) ) {
		require_once ABSPATH . 'wp-admin/includes/plugin.php';
	}
	$result = array(
		'core'    => null,
		'plugins' => array(),
		'themes'  => array(),
		'checked' => array(),
	);

	$core = get_site_transient( 'update_core' );
	if ( $core && ! empty( $core->updates ) ) {
		$result['checked']['core'] = isset( $core->last_checked ) ? (int) $core->last_checked : null;
		foreach ( $core->updates as $update ) {
			if ( isset( $update->response ) && 'upgrade' === $update->response ) {
				$result['core'] = $update->current;
				break;
			}
		}
	}

	$plugins_installed = get_plugins();
	$plugins           = get_site_transient( 'update_plugins' );
	if ( $plugins ) {
		$result['checked']['plugins'] = isset( $plugins->last_checked ) ? (int) $plugins->last_checked : null;
		if ( ! empty( $plugins->response ) ) {
			foreach ( $plugins->response as $file => $data ) {
				$result['plugins'][] = array(
					'file'    => $file,
					'name'    => isset( $plugins_installed[ $file ]['Name'] ) ? $plugins_installed[ $file ]['Name'] : $file,
					'current' => isset( $plugins_installed[ $file ]['Version'] ) ? $plugins_installed[ $file ]['Version'] : null,
					'new'     => isset( $data->new_version ) ? $data->new_version : null,
				);
			}
		}
	}

	$themes = get_site_transient( 'update_themes' );
	if ( $themes ) {
		$result['checked']['themes'] = isset( $themes->last_checked ) ? (int) $themes->last_checked : null;
		if ( ! empty( $themes->response ) ) {
			foreach ( $themes->response as $stylesheet => $data ) {
				$theme              = wp_get_theme( $stylesheet );
				$result['themes'][] = array(
					'name'    => $theme->get( 'Name' ) ? $theme->get( 'Name' ) : $stylesheet,
					'current' => $theme->get( 'Version' ),
					'new'     => isset( $data['new_version'] ) ? $data['new_version'] : null,
				);
			}
		}
	}
	return $result;
}

/**
 * Tail of the PHP error log, if logging is enabled and the file is readable.
 */
function guardian_connector_php_errors() {
	$path = null;
	if ( defined( 'WP_DEBUG_LOG' ) && is_string( WP_DEBUG_LOG ) && '' !== WP_DEBUG_LOG ) {
		$path = WP_DEBUG_LOG;
	} elseif ( defined( 'WP_DEBUG_LOG' ) && WP_DEBUG_LOG ) {
		$path = WP_CONTENT_DIR . '/debug.log';
	} elseif ( ini_get( 'error_log' ) ) {
		$path = ini_get( 'error_log' );
	}
	if ( ! $path ) {
		return array(
			'available' => false,
			'reason'    => 'PHP error logging is not enabled (set WP_DEBUG_LOG to a private path in wp-config.php)',
		);
	}
	if ( ! is_readable( $path ) ) {
		return array(
			'available' => false,
			'reason'    => 'Log file not readable by WordPress: ' . basename( $path ),
		);
	}
	$size   = filesize( $path );
	$handle = fopen( $path, 'rb' );
	if ( ! $handle ) {
		return array(
			'available' => false,
			'reason'    => 'Could not open log file',
		);
	}
	$max = 65536;
	if ( $size > $max ) {
		fseek( $handle, -$max, SEEK_END );
	}
	$chunk = stream_get_contents( $handle );
	fclose( $handle );
	$lines   = array_values( array_filter( explode( "\n", (string) $chunk ) ) );
	$lines   = array_slice( $lines, -200 );
	$entries = array_values(
		array_filter(
			$lines,
			function ( $line ) {
				return (bool) preg_match( '/PHP (Fatal error|Parse error|Warning|Notice|Deprecated)/', $line );
			}
		)
	);
	return array(
		'available'          => true,
		'publicly_reachable' => ( wp_normalize_path( $path ) === wp_normalize_path( WP_CONTENT_DIR . '/debug.log' ) ),
		'entries'            => $entries,
	);
}

function guardian_connector_status( WP_REST_Request $request ) {
	global $wp_version;

	$admins = get_users(
		array(
			'role'   => 'administrator',
			'fields' => array( 'user_login', 'user_registered' ),
		)
	);

	$data = array(
		'connector_version' => GUARDIAN_CONNECTOR_VERSION,
		'core'              => array(
			'version'   => $wp_version,
			'multisite' => is_multisite(),
		),
		'php'               => array( 'version' => PHP_VERSION ),
		'updates'           => guardian_connector_updates(),
		'php_errors'        => guardian_connector_php_errors(),
		'security'          => array(
			'administrators'    => $admins,
			'debug_display'     => defined( 'WP_DEBUG_DISPLAY' ) ? (bool) WP_DEBUG_DISPLAY : true,
			'file_edit_allowed' => ! ( defined( 'DISALLOW_FILE_EDIT' ) && DISALLOW_FILE_EDIT ),
		),
	);

	if ( $request->get_param( 'seo' ) ) {
		$data['seo'] = array(
			'rank_math'   => null,
			// "Discourage search engines" in Settings > Reading sets this to false.
			'blog_public' => (bool) get_option( 'blog_public' ),
		);
		if ( class_exists( 'RankMath' ) ) {
			$data['seo']['rank_math'] = array(
				'general' => guardian_connector_redact( get_option( 'rank-math-options-general', array() ) ),
				'titles'  => guardian_connector_redact( get_option( 'rank-math-options-titles', array() ) ),
				'sitemap' => guardian_connector_redact( get_option( 'rank-math-options-sitemap', array() ) ),
			);
		}
	}

	return rest_ensure_response( $data );
}
