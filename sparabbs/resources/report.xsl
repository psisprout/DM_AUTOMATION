<?xml version="1.0" encoding="UTF-8"?>
<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">
  <xsl:output method="html" indent="yes"/>

  <xsl:template match="/bbs_validation">
    <html>
      <head>
        <title>BBS validation report</title>
        <style>
          body { font: 13px/1.5 system-ui, sans-serif; margin: 24px; color: #1a1a1a; }
          h1 { font-size: 20px; margin: 0 0 4px; }
          h2 { font-size: 15px; margin: 28px 0 8px; }
          table { border-collapse: collapse; width: 100%; margin-bottom: 8px; }
          th, td { border: 1px solid #d8d8d8; padding: 4px 8px; text-align: right; }
          th { background: #f2f2f2; text-align: left; font-weight: 600; }
          td.l, th.l { text-align: left; }
          .PASS { color: #14691f; font-weight: 600; }
          .WARN { color: #9a6400; font-weight: 600; }
          .FAIL { color: #b3261e; font-weight: 600; }
          .meta td { text-align: left; }
          .band td:first-child { padding-left: 28px; color: #555; }
          tr.self { background: #fbfbfd; }
        </style>
      </head>
      <body>
        <h1>BBS conversion validation &#8212;
          <span class="{@status}"><xsl:value-of select="@status"/></span>
        </h1>
        <p>
          <xsl:value-of select="summary/@terms"/> Z terms,
          <xsl:value-of select="summary/@points"/> frequency points &#183;
          pass <xsl:value-of select="summary/@passed"/>,
          warn <xsl:value-of select="summary/@warned"/>,
          fail <xsl:value-of select="summary/@failed"/>
        </p>

        <h2>Inputs</h2>
        <table class="meta">
          <tr><th class="l">Reference</th><td><xsl:value-of select="meta/reference/@file"/></td></tr>
          <tr><th class="l">BBS result</th><td><xsl:value-of select="meta/bbs_result/@file"/></td></tr>
          <tr><th class="l">SPICE deck</th><td><xsl:value-of select="meta/spice/@deck"/></td></tr>
          <tr><th class="l">Command</th><td><xsl:value-of select="meta/spice/@command"/></td></tr>
          <tr><th class="l">Reference node</th><td><xsl:value-of select="meta/reference_node/@description"/></td></tr>
          <xsl:for-each select="meta/note">
            <tr><th class="l">Note</th><td><xsl:value-of select="."/></td></tr>
          </xsl:for-each>
        </table>

        <h2>Checks</h2>
        <table>
          <tr><th class="l">Check</th><th class="l">Detail</th><th>Status</th></tr>
          <xsl:for-each select="checks/check">
            <tr>
              <td class="l"><xsl:value-of select="@name"/></td>
              <td class="l"><xsl:value-of select="@detail"/></td>
              <td class="{@status}"><xsl:value-of select="@status"/></td>
            </tr>
          </xsl:for-each>
        </table>

        <h2>Z terms</h2>
        <table>
          <tr>
            <th class="l">Term / band</th><th class="l">Ports</th>
            <th>max err %</th><th>max dB</th><th>RMSE dB</th>
            <th>max phase&#176;</th><th>worst @ Hz</th><th>Status</th>
          </tr>
          <xsl:for-each select="terms/term">
            <tr class="{@kind}">
              <td class="l"><b><xsl:value-of select="@name"/></b></td>
              <td class="l"><xsl:value-of select="@port_i"/> / <xsl:value-of select="@port_j"/></td>
              <td colspan="5"></td>
              <td class="{@status}"><xsl:value-of select="@status"/></td>
            </tr>
            <xsl:for-each select="band">
              <tr class="band">
                <td class="l"><xsl:value-of select="@name"/></td>
                <td class="l"><xsl:value-of select="@points"/> pts</td>
                <td><xsl:value-of select="@norm_err_pct"/></td>
                <td><xsl:value-of select="@max_err_db"/></td>
                <td><xsl:value-of select="@rmse_db"/></td>
                <td><xsl:value-of select="@max_phase_deg"/></td>
                <td><xsl:value-of select="@f_worst"/></td>
                <td class="{@status}"><xsl:value-of select="@status"/></td>
              </tr>
            </xsl:for-each>
            <xsl:for-each select="resonance">
              <tr class="band">
                <td class="l">peak @ <xsl:value-of select="@ref_f"/> Hz</td>
                <td class="l">shift <xsl:value-of select="@shift_pct"/> %</td>
                <td colspan="2"><xsl:value-of select="@ref_mag_ohm"/> &#8594; <xsl:value-of select="@dut_mag_ohm"/> &#937;</td>
                <td colspan="3">|Z| err <xsl:value-of select="@mag_err_pct"/> %</td>
                <td class="{@status}"><xsl:value-of select="@status"/></td>
              </tr>
            </xsl:for-each>
          </xsl:for-each>
        </table>

        <h2>Criteria</h2>
        <table class="meta">
          <tr><th class="l">max |Z| error</th><td><xsl:value-of select="criteria/@mag_err_pct"/> % of band peak</td></tr>
          <tr><th class="l">max dB error</th><td><xsl:value-of select="criteria/@err_db"/> dB</td></tr>
          <tr><th class="l">max phase error</th><td><xsl:value-of select="criteria/@phase_err_deg"/> deg</td></tr>
          <tr><th class="l">peak shift</th><td><xsl:value-of select="criteria/@peak_shift_pct"/> %</td></tr>
          <tr><th class="l">peak |Z| error</th><td><xsl:value-of select="criteria/@peak_mag_err_pct"/> %</td></tr>
        </table>
      </body>
    </html>
  </xsl:template>
</xsl:stylesheet>
