ALTER TABLE dbo.HlsKeys
ADD purpose VARCHAR(32) NOT NULL
    CONSTRAINT DF_HlsKeys_Purpose
    DEFAULT 'personalization';
GO

ALTER TABLE dbo.HlsKeys
ADD CONSTRAINT CK_HlsKeys_Purpose
    CHECK (
        purpose IN (
            'personalization',
            'service'
        )
    );
GO

CREATE INDEX IX_HlsKeys_Purpose_Status_CreatedAt
    ON dbo.HlsKeys (
        purpose,
        status,
        created_at DESC
    );
GO